import json
import time
import urllib.error
import urllib.request

BUSINESS = "http://127.0.0.1:8001"
CUSTOMER = "http://127.0.0.1:8000"
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


def send(token, message_id, payload_type, content):
    return call(
        "POST",
        f"{CUSTOMER}/api/v1/chat/messages",
        {"message_id": message_id, "type": payload_type, "content": content},
        {"Authorization": f"Bearer {token}"},
    )


def history(token):
    return call("GET", f"{CUSTOMER}/api/v1/chat/history", None, {"Authorization": f"Bearer {token}"})[1]


if __name__ == "__main__":
    token = login("user_001")
    suffix = str(int(time.time()))
    print(send(token, f"m_q_{suffix}", "text", {"text": "我想申请退款"}))
    time.sleep(3)
    print(send(token, f"m_obj_{suffix}", "object", {"object_type": "order", "object_id": "ORDER_001"}))
    time.sleep(3)
    print(send(token, f"m_reason_{suffix}", "text", {"text": "买错套餐了"}))
    time.sleep(4)
    for item in history(token)[-6:]:
        print("-", item["role"], item["content"])
