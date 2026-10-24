from datetime import UTC, datetime
from uuid import uuid4


def get_uid(prefix: str) -> str:
    """生成带业务前缀的稳定格式编号。"""
    return f"{prefix}_{uuid4().hex[:12]}"


def get_utcnow() -> datetime:
    return datetime.now(UTC)
