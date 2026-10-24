# -*- coding: utf-8 -*-
"""人工接管闭环端到端探测：接入（含抢单冲突）→ 回复 → 结束 → AI 恢复。

前置条件：
- legal-business-service 运行在 8001（演示账号签发）
- customer-service API 运行在 8010（工单/会话/消息）
- 演示库中存在一条待接入工单（可先由 AI 兜底或显式转人工产生）

用法：
    python tests/e2e_human_handoff.py
"""

import json
import sys
import urllib.error
import urllib.request

LEGAL_BASE = "http://127.0.0.1:8001/api/v1"
CUSTOMER_BASE = "http://127.0.0.1:8010/api/v1"

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

STEP = 0


def step(title):
    global STEP
    STEP += 1
    print(f"\n[{STEP}] {title}")


def request(method, url, token=None, payload=None, expect_error=False):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with OPENER.open(req, timeout=15) as resp:
            body = resp.read().decode()
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        parsed = json.loads(body) if body else None
        if not expect_error:
            raise
        return exc.code, parsed


def login(user_id, role):
    status, body = request(
        "POST", f"{LEGAL_BASE}/auth/demo-token", payload={"user_id": user_id, "role": role},
    )
    assert status == 200, f"登录失败 {user_id}: {status} {body}"
    return body["access_token"]


def check(name, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"    [{mark}] {name}" + (f" —— {detail}" if detail else ""))
    if not condition:
        sys.exit(1)


def main():
    # ------------------------------------------------------------------ 准备
    step("准备：客户与客服登录")
    user_token = login("user_001", "customer")
    agent_token = login("agent_001", "agent")
    agent2_token = login("agent_002", "agent")
    print("    user_001 / agent_001 / agent_002 令牌已签发")

    # ---------------------------------------------- 制造工单（显式转人工）
    step("客户显式转人工：应创建工单并暂停 AI")
    status, handoffs_before = request("GET", f"{CUSTOMER_BASE}/handoffs", token=agent_token)
    known = {h["id"] for h in handoffs_before}
    print(f"    转人工前队列 {len(known)} 条")
    import uuid
    transfer_id = f"ho-{uuid.uuid4().hex[:12]}"
    status, sent = request(
        "POST", f"{CUSTOMER_BASE}/chat/messages",
        token=user_token,
        payload={"message_id": transfer_id, "type": "text", "content": {"text": "我涉及具体案件纠纷，需要转人工客服处理"}},
    )
    check("转人工消息已受理", status in (200, 201), f"status={status}")
    handoff_created = bool((sent or {}).get("handoff_created"))
    print(f"    handoff_created={handoff_created}")

    # ---------------------------------------------- 工单列表与归属
    step("查询待接入工单队列")
    status, handoffs = request("GET", f"{CUSTOMER_BASE}/handoffs", token=agent_token)
    created = [h for h in handoffs if h["id"] not in known]
    # 优先用受理响应里的会话定位工单（同会话已有开放工单时会被复用）
    conv_id = (sent or {}).get("conversation_id")
    target = (next((h for h in handoffs if h["conversation_id"] == conv_id), None)
              or created[0]
              or next((h for h in handoffs if h.get("status") == "waiting"), None))
    assert target, "没有可用的待接入工单"
    check("定位到本次转人工工单", bool(created) or target["conversation_id"] == conv_id,
          f"队列 {len(handoffs)} 条，新建 {len(created)} 条")
    handoff_id = target["id"]
    conversation_id = target["conversation_id"]
    print(f"    工单 {handoff_id} 会话 {conversation_id} 状态 {target.get('status')}")

    # ---------------------------------------------- 抢单冲突
    step("抢单冲突：agent_002 先接单，agent_001 再接应被拒绝")
    status, _ = request("POST", f"{CUSTOMER_BASE}/handoffs/{handoff_id}/accept", token=agent2_token)
    check("agent_002 接单成功", status == 204, f"status={status}")

    status, body = request(
        "POST", f"{CUSTOMER_BASE}/handoffs/{handoff_id}/accept",
        token=agent_token, payload={}, expect_error=True,
    )
    check("agent_001 重复接单被拒", status == 403, f"status={status} body={body}")

    # 原负责人重复接单应幂等成功
    status, _ = request("POST", f"{CUSTOMER_BASE}/handoffs/{handoff_id}/accept", token=agent2_token)
    check("agent_002 重复接单幂等", status == 204, f"status={status}")

    # ---------------------------------------------- 人工接管期间 AI 暂停
    step("人工接管期间：会话模式为 HUMAN，AI 自动回复暂停")
    status, detail = request(
        "GET", f"{CUSTOMER_BASE}/conversations/{conversation_id}", token=agent2_token,
    )
    check("客服可查看会话", status == 200)
    check("会话模式为 HUMAN", detail.get("mode") == "HUMAN", f"mode={detail.get('mode')}")
    check("会话包含交接上下文", bool(detail.get("handoff_context")), "context 已生成")

    # 客户在人工期间发消息：仅保存，不触发 AI
    status, sent = request(
        "POST", f"{CUSTOMER_BASE}/chat/messages",
        token=user_token,
        payload={"message_id": f"hm-{uuid.uuid4().hex[:12]}", "type": "text", "content": {"text": "有人吗，我等下提供材料"}},
    )
    check("客户消息已保存", status in (200, 201), f"status={status}")
    before = detail.get("messages") or []
    status, detail2 = request(
        "GET", f"{CUSTOMER_BASE}/conversations/{conversation_id}", token=agent2_token,
    )
    after = detail2.get("messages") or []
    check("人工期间消息只保存不生成 AI 回复",
          len(after) == len(before) + 1 and all(m.get("role") != "ai" for m in after[len(before):]),
          f"消息数 {len(before)} -> {len(after)}")

    # ---------------------------------------------- 人工回复
    step("人工客服回复客户")
    status, _ = request(
        "POST", f"{CUSTOMER_BASE}/handoffs/{handoff_id}/messages",
        token=agent2_token,
        payload={"message_id": f"hr-{uuid.uuid4().hex[:12]}", "text": "您好，我是客服小林。请在服务页面补充合同原件与对方主体信息，我同步帮您跟进。"},
    )
    check("人工回复成功", status == 204, f"status={status}")

    # ---------------------------------------------- 结束工单
    step("结束工单：会话回到 AI，流程标记需重新校验")
    status, flow_before = request(
        "GET", f"{CUSTOMER_BASE}/conversations/{conversation_id}", token=agent2_token,
    )
    version_before = (flow_before.get("flow_summary") or {}).get("state_version")
    status, _ = request(
        "POST", f"{CUSTOMER_BASE}/handoffs/{handoff_id}/resolve",
        token=agent2_token,
        payload={"resolution_note": "已告知客户在服务页面补充材料，问题引导回自助流程。"},
    )
    check("工单已结束", status == 204, f"status={status}")

    status, detail3 = request(
        "GET", f"{CUSTOMER_BASE}/conversations/{conversation_id}", token=agent2_token,
    )
    check("会话模式回到 AI", detail3.get("mode") == "AI", f"mode={detail3.get('mode')}")
    flow_after = detail3.get("flow_summary") or {}
    if flow_after:
        check("FlowState 版本已递增（需重新校验）",
              flow_after.get("state_version", 0) > (version_before or 0),
              f"{version_before} -> {flow_after.get('state_version')}")
    else:
        print("    [INFO] 该会话无在办流程（工单为兜底建单），跳过版本校验")

    # ---------------------------------------------- AI 恢复
    step("工单结束后客户再次提问：AI 恢复自动回复")
    status, sent = request(
        "POST", f"{CUSTOMER_BASE}/chat/messages",
        token=user_token,
        payload={"message_id": f"hr-{uuid.uuid4().hex[:12]}", "type": "text", "content": {"text": "请查询订单 ORDER_002 的合同审核办理进度"}},
    )
    check("消息已受理", status in (200, 201), f"status={status}")
    print("    已发送消息，等待 Turn Worker / AI / 结算 ...")

    import time
    known_ids = {m.get("message_id") for m in detail3.get("messages", [])}
    ai_replied = False
    for _ in range(30):
        time.sleep(2)
        status, detail4 = request(
            "GET", f"{CUSTOMER_BASE}/conversations/{conversation_id}", token=agent2_token,
        )
        msgs = detail4.get("messages") or []
        fresh_ai = [m for m in msgs if m.get("role") == "ai" and m.get("message_id") not in known_ids]
        if fresh_ai:
            ai_replied = (not fresh_ai[-1].get("content", {}).get("fallback") and fresh_ai[-1].get("content", {}).get("reply_type") != "REQUEST_HANDOFF")
            print(f"    AI 回复：{json.dumps(fresh_ai[-1].get('content'), ensure_ascii=False)[:160]}")
            break
    check("AI 恢复自动回复", ai_replied)

    print("\n=== 人工接管闭环端到端探测通过 ===")


if __name__ == "__main__":
    main()
