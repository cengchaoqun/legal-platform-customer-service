import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from atguigu.agent.factory import create_policy_agent, create_support_agent
from atguigu.agent.harness.errors import AgentExecutionError, TerminalErrorCode
from atguigu.agent.harness.run.executor import AgentExecutor
from atguigu.agent.harness.run.fixture_agent import FixtureAgentRunner
from atguigu.agent.harness.run.policy_runner import (
    DeployedPolicyPredictor,
    FixturePolicyPredictorAdapter,
)
from atguigu.agent.harness.tools.service import TOOL_SERVICE
from atguigu.agent.llm.adapter import ModelAdapter, ModelStatus
from atguigu.app.routers import admin, run
from atguigu.app.schemas.run import AgentRunRequest
from atguigu.common.config import get_settings

logger = logging.getLogger(__name__)


class UnavailablePolicyPredictor:
    """DeepSeek 模型不可用时的显式失败，绝不回退到外部模型。"""

    def __init__(self, detail: str):
        self.detail = detail

    async def predict(self, request: AgentRunRequest):  # noqa: ANN001
        raise AgentExecutionError(
            TerminalErrorCode.MODEL_CALL_FAILED,
            f"DeepSeek 模型不可用：{self.detail}",
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.model_status = await ModelAdapter.check_deployment()

    if settings.policy_runtime_mode == "fixture":
        logger.warning("AI Service 以 FIXTURE 决策器启动：仅用于开发测试，不是上线模型")
        app.state.policy_predictor = FixturePolicyPredictorAdapter()
        app.state.agent_runner = None
        app.state.fixture_runner = FixtureAgentRunner(TOOL_SERVICE)
    elif app.state.model_status.reachable:
        app.state.policy_predictor = DeployedPolicyPredictor(create_policy_agent())
        app.state.agent_runner = create_support_agent()
        app.state.fixture_runner = None
    else:
        logger.error("DeepSeek 模型不可用，服务以固定兜底模式运行：%s", app.state.model_status.detail)
        app.state.policy_predictor = UnavailablePolicyPredictor(
            app.state.model_status.detail
        )
        app.state.agent_runner = None
        app.state.fixture_runner = None

    yield

    logger.info("AI Service 关闭")


app = FastAPI(title="Legal AI Customer Service", lifespan=lifespan)

app.include_router(run.router)
app.include_router(admin.router)

app.add_middleware(
    CORSMiddleware,
    # vite preview 构建产物预览端口
    allow_origins=[
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:4174",
        "http://127.0.0.1:4174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    """运行状态：明确暴露当前模型模式，避免把 fixture 误认为上线模型。"""
    status: ModelStatus = app.state.model_status
    return {
        "status": "ok" if status.reachable else "degraded",
        "service": "ai-service",
        "runtime_mode": status.runtime_mode,
        "model_name": status.model_name,
        "model_artifact_version": status.artifact_version,
        "model_reachable": status.reachable,
        "detail": status.detail,
        "checked_at": status.checked_at,
    }
