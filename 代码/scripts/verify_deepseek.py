"""真实 DeepSeek 验收。需启动三个服务；会生成审计，不提交退款/开票申请。"""
import json
import time
import uuid
from pathlib import Path

import httpx


def main():
    results = []
    with httpx.Client(timeout=90, trust_env=False) as client:
        ai, business = "http://127.0.0.1:8002", "http://127.0.0.1:8001"
        health = client.get(ai + "/health").raise_for_status().json()
        assert health["runtime_mode"] == "DEEPSEEK" and health["model_reachable"], health
        assert client.get(business + "/health").json()["mode"] == "demo"

        def login(uid, role):
            return client.post(business + "/api/v1/auth/demo-token", json={"user_id": uid, "role": role}).raise_for_status().json()["access_token"]

        headers = {"Authorization": "Bearer " + login("user_001", "customer"), "X-Internal-Service-Token": "legal-support-internal-token"}
        admin = {"Authorization": "Bearer " + login("admin_001", "admin")}
        conversation = "accept_" + uuid.uuid4().hex

        def run(name, text, intent, expected_tool=None, flow=None):
            turn = "accept_" + uuid.uuid4().hex
            body = dict(contract_version="legal-support-v2", request_id=turn + ":1", conversation_id=conversation,
                        turn_id=turn, user_id="user_001", input_revision=1,
                        flow_state_version=flow["state_version"] if flow else 0, flow_state=flow,
                        messages=[dict(message_id=turn + "m", type="text", content={"text": text})], history=[])
            start = time.monotonic()
            event = client.post(ai + "/internal/v1/agent/runs", json=body, headers=headers).raise_for_status().json()
            run_id = event["event_data"]["run_id"]
            if event["event_type"] == "run_decision_prepared":
                event = client.post(ai + f"/internal/v1/agent/runs/{run_id}/commit", json={"input_revision": 1}, headers=headers).raise_for_status().json()
            audit = client.get(ai + f"/api/v1/runs/{run_id}", headers=admin).raise_for_status().json()
            record = audit["run"]
            tools = [t["tool_name"] for t in audit["tool_calls"] if t["success"]]
            passed = (event["event_type"] != "run_failed" and record["model_name"] == health["model_name"]
                      and record["input_tokens"] > 0 and (record["policy_decision"] or {}).get("primary_intent") == intent
                      and (expected_tool is None or expected_tool in tools))
            result = dict(name=name, input=text, passed=passed, event=event, run_id=run_id,
                          intent=(record["policy_decision"] or {}).get("primary_intent"), tools=tools,
                          input_tokens=record["input_tokens"], output_tokens=record["output_tokens"],
                          elapsed_seconds=round(time.monotonic()-start, 2), error=record.get("error"))
            results.append(result)
            print(name, "PASS" if passed else "FAIL", tools, flush=True)
            return event

        first = run("缺订单澄清", "合同审核套餐我想退款", "REFUND_APPLY")
        update = first["event_data"].get("flow_update", {}).get("changes")
        if update:
            flow = dict(flow_id="accept-flow", flow_type=update["flow_type"], node=update["node"], slots=update["slots"],
                        pending_intents=update["pending_intents"], clarification_attempts=update["clarification_attempts"],
                        state_version=1, flow_definition_version="legal-support-flow-v1")
            run("多轮补充订单", "ORDER_001，买错套餐了", "REFUND_APPLY", "get_order", flow)
        run("订单查询", "查询订单 ORDER_001 的金额和状态", "ORDER_QUERY", "get_order")
        run("办理进度", "ORDER_002 的合同审核进行到哪一步了", "SERVICE_PROGRESS_QUERY", "get_service_progress")
        run("发票查询", "ORDER_001 的发票开好了吗", "INVOICE_QUERY", "get_invoice")
        run("发票申请入口", "我要给 ORDER_001 申请开票", "INVOICE_APPLY", "get_order")
        run("政策检索", "退款政策和到账时间怎么规定的", "POLICY_QUERY", "search_knowledge")
        run("投诉升级", "我要投诉服务延误，请升级处理", "COMPLAINT")
        run("专业问题升级", "我这个合同纠纷能胜诉吗，帮我判断法律责任", "LEGAL_ADVICE_REQUEST")

    report = dict(model=health["model_name"], runtime_mode=health["runtime_mode"], business="demo",
                  scope="真实模型和只读业务工具验收；commit 仅发布页面入口，不执行业务写操作",
                  passed=all(r["passed"] for r in results) and len(results)==9, results=results)
    path = Path(__file__).resolve().parents[1] / "reports/deepseek-acceptance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{sum(r['passed'] for r in results)}/{len(results)} passed; {path}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
