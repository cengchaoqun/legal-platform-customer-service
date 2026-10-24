import asyncio
import os
import sys

import psycopg
import pytest

# 测试使用独立数据库，避免影响演示数据与正在运行的 Worker
TEST_DB = "customer_service_test"
os.environ["DATABASE_URL"] = (
    f"postgresql+psycopg://legal:legal_dev_password@127.0.0.1:55432/{TEST_DB}"
)
os.environ["AI_SERVICE_URL"] = "http://127.0.0.1:9"  # 故意不可达，测试不依赖 AI 服务
os.environ["REDIS_URL"] = "redis://127.0.0.1:56379/15"
os.environ["AI_FALLBACK_HANDOFF"] = "true"

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _ensure_database() -> None:
    dsn = "host=127.0.0.1 port=55432 user=legal password=legal_dev_password dbname=postgres"
    with psycopg.connect(dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,))
            if cursor.fetchone() is None:
                cursor.execute(f'CREATE DATABASE "{TEST_DB}"')


_ensure_database()

from atguigu.common.event_loop import run_async  # noqa: E402
from atguigu.infrastucture.init_db import init_database  # noqa: E402

run_async(init_database())

from atguigu.infrastucture.db import session_factory  # noqa: E402
from sqlalchemy import text  # noqa: E402

TABLES = (
    "realtime_outbox, conversation_flow_states, handoffs, messages, "
    "conversation_turns, conversations"
)


@pytest.fixture(autouse=True)
async def clean_database():
    async with session_factory() as session:
        await session.execute(text(f"TRUNCATE TABLE {TABLES} RESTART IDENTITY CASCADE"))
        await session.commit()
    yield
    async with session_factory() as session:
        await session.execute(text(f"TRUNCATE TABLE {TABLES} RESTART IDENTITY CASCADE"))
        await session.commit()


@pytest.fixture
async def session():
    async with session_factory() as db_session:
        yield db_session
        await db_session.rollback()
