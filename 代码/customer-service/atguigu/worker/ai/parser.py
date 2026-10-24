from typing import Any

from atguigu.app.schemas.event import AgentEventType


class AIEventParser:
    """解析 AI Service 返回的最终事件。

    只提取业务需要的最小字段；内部流程补丁交给结算服务做版本与白名单校验。
    """

    @staticmethod
    def find_run_id(event: dict[str, Any]) -> str:
        run_id = event.get("event_data", {}).get("run_id")
        if run_id:
            return run_id
        raise ValueError("AI Service 没有返回run_id")

    @staticmethod
    def has_prepared_decision(event: dict[str, Any]) -> bool:
        return event.get("event_type") == AgentEventType.RUN_DECISION_PREPARED

    @staticmethod
    def parse_outcome(event: dict[str, Any]) -> dict[str, Any]:
        event_type = event["event_type"]
        event_data = event.get("event_data", {})

        if event_type == AgentEventType.RUN_FAILED:
            raise RuntimeError(
                str(event_data.get("message") or "AI Service 处理失败")
            )

        if event_type == AgentEventType.RUN_COMPLETED:
            return {
                **event_data,
                "outcome_type": "message",
                "flow_update": event_data.get("flow_update"),
            }

        if event_type == AgentEventType.RUN_HANDOFF_REQUESTED:
            return {
                **event_data,
                "outcome_type": "handoff",
                "summary": str(event_data.get("summary") or "用户请求人工客服"),
                "reason_code": str(event_data.get("reason_code") or "USER_REQUESTED"),
                "queue_code": str(event_data.get("queue_code") or "platform_aftersales"),
                "handoff_context": event_data.get("handoff_context"),
                # message：给用户看的转人工提示，简短且不含内部细节
                "content": {
                    "text": str(
                        event_data.get("message") or "正在为你转接人工客服，请稍候。"
                    ),
                    "reply_type": "REQUEST_HANDOFF",
                },
                "flow_update": event_data.get("flow_update"),
            }

        raise RuntimeError("AI Service 没有返回最终处理结果")

    # 兼容旧调用名
    parser_outcome = parse_outcome
