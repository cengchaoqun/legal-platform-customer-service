import json
import time
import urllib.request

BASE = "http://127.0.0.1:8002"
BUSINESS = "http://127.0.0.1:8001"
SUFFIX = str(int(time.time() * 1000))
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post(url, body, headers=None):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json", **(headers or {})}
    )
    with OPENER.open(req) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def token():
    result = post(f"{BUSINESS}/api/v1/auth/demo-token", {"user_id": "user_001", "role": "customer"})
    return result["access_token"]


def run(token_value, tag, messages, flow_state=None, version=0, revision=1):
    request_id = f"turn_{tag}_{SUFFIX}:attempt:1"
    body = {
        "contract_version": "legal-support-v2",
        "request_id": request_id,
        "conversation_id": f"conv_probe_{SUFFIX}",
        "turn_id": f"turn_{tag}_{SUFFIX}",
        "user_id": "user_001",
        "input_revision": revision,
        "flow_state_version": version,
        "flow_state": flow_state,
        "messages": messages,
        "history": [],
    }
    event = post(
        f"{BASE}/internal/v1/agent/runs",
        body,
        {
            "Authorization": f"Bearer {token_value}",
            "X-Internal-Service-Token": "legal-support-internal-token",
        },
    )
    data = event.get("event_data", {})
    print("\n==", tag, event.get("event_type"))
    print("text:", data.get("content", {}).get("text") or data.get("message"))
    print("action:", (data.get("content", {}).get("action") or {}).get("href"))
    print("choices:", [(c["object_id"], c["label"]) for c in data.get("content", {}).get("choices", [])])
    print("flow:", json.dumps(data.get("flow_update", {}).get("changes", {}), ensure_ascii=False)[:500])
    return event


if __name__ == "__main__":
    t = token()
    first = run(t, "refund_multi", [{"message_id": f"m1{SUFFIX}", "type": "text", "content": {"text": "合同审核套餐我想退款"}}])
    flow = first["event_data"]["flow_update"]
    state = {
        "flow_id": "flow_probe",
        "flow_type": flow["changes"]["flow_type"],
        "node": flow["changes"]["node"],
        "slots": flow["changes"]["slots"],
        "pending_intents": flow["changes"]["pending_intents"],
        "clarification_attempts": flow["changes"]["clarification_attempts"],
        "state_version": 1,
        "flow_definition_version": "legal-support-flow-v1",
        "last_applied_turn_id": f"turn_refund_multi_{SUFFIX}",
    }
    run(
        t,
        "refund_pick",
        [{"message_id": f"m2{SUFFIX}", "type": "object", "content": {"object_type": "order", "object_id": "ORDER_002"}}],
        state,
        1,
        2,
    )
    run(t, "policy", [{"message_id": f"m3{SUFFIX}", "type": "text", "content": {"text": "你们的退款政策是什么"}}])
    run(t, "invoice_query", [{"message_id": f"m4{SUFFIX}", "type": "text", "content": {"text": "我的订单发票开好了吗"}}])
    run(t, "progress", [{"message_id": f"m5{SUFFIX}", "type": "text", "content": {"text": "合同审核进行到哪一步了"}}])
    run(t, "complaint", [{"message_id": f"m6{SUFFIX}", "type": "text", "content": {"text": "我要投诉，服务太差了"}}])
    run(t, "legal", [{"message_id": f"m7{SUFFIX}", "type": "text", "content": {"text": "你看我这个官司能赢吗"}}])
    run(t, "greeting", [{"message_id": f"m8{SUFFIX}", "type": "text", "content": {"text": "你好"}}])
