# -*- coding: utf-8 -*-
"""Customer Service 与 AI Service 之间的契约镜像校验。

两个服务分别持有 FlowState / FlowPatch / 意图与节点清单，任何一侧
单独漂移都会导致结算阶段的补丁被拒绝。本文件直接导入两侧模块，
逐项对比关键枚举与白名单，作为 P2 契约基线。

运行方式（在 代码/ 目录下）：
    python -m pytest tests/contract -q
"""

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CUSTOMER = ROOT / "customer-service"
AI = ROOT / "ai-service"


def _load_customer():
    """以独立名字加载纯契约文件，不污染同名 atguigu 包和其他测试。"""
    name="_customer_flow_contract_mirror"
    spec=importlib.util.spec_from_file_location(name,CUSTOMER/"atguigu/app/schemas/chat/flow.py")
    module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module
    spec.loader.exec_module(module)
    return module


def _load_ai():
    """AI 使用正常包名；不再删除其他测试已加载的模块。"""
    if str(AI) not in sys.path:
        sys.path.insert(0,str(AI))
    pytest.importorskip("atguigu.agent", reason="ai-service 不在当前测试路径")
    return (
        importlib.import_module("atguigu.agent.harness.flows.contract"),
        importlib.import_module("atguigu.agent.llm.policy"),
    )


customer_flow = _load_customer()
ai_contract, ai_policy = _load_ai()


def test_flow_definition_version_matches():
    """两侧必须使用同一个流程定义版本，否则结算一律拒绝。"""
    assert customer_flow.FLOW_DEFINITION_VERSION == ai_contract.FLOW_DEFINITION_VERSION


def test_intent_sets_match():
    """模型可输出的意图集合与 Customer 端补丁白名单一致。"""
    ai_intents = {intent.value for intent in ai_policy.PolicyIntent}
    customer_intents = set(customer_flow.INTENTS)
    assert ai_intents == customer_intents, (
        f"AI 独有: {sorted(ai_intents - customer_intents)}; "
        f"Customer 独有: {sorted(customer_intents - ai_intents)}"
    )


def test_flow_nodes_match():
    """流程节点清单一致，保证节点推进白名单两侧同步。"""
    ai_nodes = set(ai_contract.FLOW_NODES)
    customer_nodes = set(customer_flow.FLOW_NODES)
    assert ai_nodes == customer_nodes, (
        f"AI 独有: {sorted(ai_nodes - customer_nodes)}; "
        f"Customer 独有: {sorted(customer_nodes - ai_nodes)}"
    )


def test_flow_patch_whitelist_matches():
    """FlowPatch 白名单字段一致：模型产出的补丁键必须能被 Customer 接受。"""
    ai_fields = set(ai_contract.FlowPatchChanges.model_fields.keys())
    customer_fields = set(customer_flow.FlowPatchChanges.model_fields.keys())
    assert ai_fields == customer_fields, (
        f"AI 独有: {sorted(ai_fields - customer_fields)}; "
        f"Customer 独有: {sorted(customer_fields - ai_fields)}"
    )


def test_flow_snapshot_shape_matches():
    """FlowSnapshot 关键字段一致：输入修订与状态版本在两侧语义相同。"""
    ai_fields = set(ai_contract.FlowSnapshot.model_fields.keys())
    customer_fields = set(customer_flow.FlowSnapshot.model_fields.keys())
    assert ai_fields == customer_fields


def test_flow_patch_roundtrip():
    """同一补丁在两侧都能通过校验，且未知字段被拒绝。"""
    payload = {
        "flow_type": "REFUND_APPLY",
        "node": "COLLECT_REASON",
        "slots": {},
        "pending_intents": [],
        "clarification_attempts": {},
    }
    ai_patch = ai_contract.FlowPatchChanges(**payload)
    customer_patch = customer_flow.FlowPatchChanges(**payload)
    assert ai_patch.flow_type == customer_patch.flow_type == "REFUND_APPLY"

    bad = dict(payload, unexpected_key="x")
    with pytest.raises(Exception):
        ai_contract.FlowPatchChanges(**bad)
    with pytest.raises(Exception):
        customer_flow.FlowPatchChanges(**bad)


def test_contract_version_literal():
    """请求契约版本固定为 legal-support-v2。"""
    import atguigu.app.schemas.run as ai_run

    payload = {
        "request_id": "req_contract_test",
        "conversation_id": "conv_test",
        "turn_id": "turn_test",
        "user_id": "user_001",
        "input_revision": 1,
        "flow_state_version": 0,
        "messages": [
            {"message_id": "msg_ct_1", "type": "text", "content": {"text": "你好"}}
        ],
    }
    req = ai_run.AgentRunRequest(**payload)
    assert req.contract_version == "legal-support-v2"
    with pytest.raises(Exception):
        ai_run.AgentRunRequest(**dict(payload, contract_version="legal-support-v1"))
