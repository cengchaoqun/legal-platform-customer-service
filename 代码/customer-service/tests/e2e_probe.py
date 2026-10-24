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


def send(token, message_id, text):
    return call(
        "POST",
        f"{CUSTOMER}/api/v1/chat/messages",
        {"message_id": message_id, "type": "text", "content": {"text": text}},
        {"Authorization": f"Bearer {token}"},
    )


def history(token, after=None):
    url = f"{CUSTOMER}/api/v1/chat/history" + (f"?after_sequence={after}" if after else "")
    return call("GET", url, None, {"Authorization": f"Bearer {token}"})


if __name__ == "__main__":
    token = login("user_001")
    suffix = str(int(time.time()))
    status, body = send(token, f"msg_e2e_{suffix}", "合同审核套餐我想退款")
    print("send:", status, body)
    time.sleep(4)
    _, messages = history(token)
    for item in messages[-4:]:
        print("-", item["role"], item["content"])
