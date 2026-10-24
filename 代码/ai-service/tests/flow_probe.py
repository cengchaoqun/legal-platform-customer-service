import json
import time
import urllib.request

BASE = "http://127.0.0.1:8002"
BUSINESS = "http://127.0.0.1:8001"
SUFFIX = str(int(time.time() * 1000))
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})}
    )
    try:
        with OPENER.open(req) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def login(user_id):
    _, result = call("POST", f"{BUSINESS}/api/v1/auth/demo-token", {"user_id": user_id, "role": "customer"})
    return result["access_token"]


def agent_run(token, turn_id, text, flow=None, version=0, revision=1):
    body = {
        "contract_version": "legal-support-v2",
        "request_id": f"{turn_id}:attempt:1",
        "conversation_id": f"conv_flow_{SUFFIX}",
        "turn_id": turn_id,
        "user_id": "user_001",
        "input_revision": revision,
        "flow_state_version": version,
        "flow_state": flow,
        "messages": [{"message_id": f"{turn_id}_m", "type": "text", "content": {"text": text}}],
        "history": [],
    }
    _, event = call(
        "POST",
        f"{BASE}/internal/v1/agent/runs",
        body,
        {"Authorization": f"Bearer {token}", "X-Internal-Service-Token": "legal-support-internal-token"},
    )
    return event


if __name__ == "__main__":
    token = login("user_001")
    turn1 = f"turn_flow_1_{SUFFIX}"
    event = agent_run(token, turn1, "ORDER_001 买错套餐了，我想退款")
    data = event["event_data"]
    print("1) type:", event["event_type"])
    run_id = data["run_id"]

    status, committed = call(
        "POST",
        f"{BASE}/internal/v1/agent/runs/{run_id}/commit",
        {"input_revision": 1},
        {"Authorization": f"Bearer {token}", "X-Internal-Service-Token": "legal-support-internal-token"},
    )
    print("2) commit:", status, committed.get("event_type"))
    print("   text:", committed.get("event_data", {}).get("content", {}).get("text"))
    print("   href:", (committed.get("event_data", {}).get("content", {}).get("action") or {}).get("href"))
    flow = committed["event_data"]["flow_update"]

    # 用户在页面确认提交
    status, applied = call(
        "POST",
        f"{BUSINESS}/api/v1/orders/ORDER_001/after-sales",
        {"kind": "REFUND", "reason": "买错套餐", "requested_amount": "299.00"},
        {"Authorization": f"Bearer {token}", "Idempotency-Key": f"demo-refund-{SUFFIX}"},
    )
    print("3) page submit:", status, applied.get("data"))

    # 下一轮查询进度（使用已保存的流程状态）
    state = {
        "flow_id": "flow_flow",
        "flow_type": flow["changes"]["flow_type"],
        "node": flow["changes"]["node"],
        "slots": flow["changes"]["slots"],
        "pending_intents": flow["changes"]["pending_intents"],
        "clarification_attempts": flow["changes"]["clarification_attempts"],
        "state_version": 2,
        "flow_definition_version": "legal-support-flow-v1",
        "last_applied_turn_id": turn1,
    }
    turn2 = f"turn_flow_2_{SUFFIX}"
    event2 = agent_run(token, turn2, "好的，退款进度怎么样了", state, 2, 2)
    data2 = event2["event_data"]
    print("4) type:", event2["event_type"])
    print("   text:", data2.get("content", {}).get("text"))
    print("   action:", (data2.get("content", {}).get("action") or {}).get("href"))
    print("   node:", data2.get("flow_update", {}).get("changes", {}).get("node"))
