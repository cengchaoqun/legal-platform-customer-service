"""清空演示数据，便于重复演示与自动化测试。

只删除本地演示数据库中的数据，不触碰任何真实平台数据；
执行后重启业务服务会重新导入种子数据。
"""

import sys

import psycopg

DSN = "host=127.0.0.1 port=55432 user=legal password=legal_dev_password dbname={db}"

TABLES = {
    "legal_business": [
        "idempotency_records",
        "after_sales",
        "invoices",
        "service_progress_events",
        "orders",
        "plans",
        "users",
    ],
    "customer_service": [
        "realtime_outbox",
        "conversation_flow_states",
        "handoffs",
        "messages",
        "conversation_turns",
        "conversations",
    ],
    "ai_customer_service": ["agent_tool_calls", "agent_runs"],
}


def reset() -> None:
    for database, tables in TABLES.items():
        with psycopg.connect(DSN.format(db=database), autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "TRUNCATE TABLE " + ", ".join(tables) + " RESTART IDENTITY CASCADE"
                )
        print(f"已清空 {database}: {', '.join(tables)}")


if __name__ == "__main__":
    try:
        reset()
    except Exception as exc:  # noqa: BLE001
        print(f"重置失败：{exc}", file=sys.stderr)
        raise
    print("完成。重启 legal-business-service 会重新导入演示种子数据。")
