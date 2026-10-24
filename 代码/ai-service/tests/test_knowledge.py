from datetime import date

import pytest

from atguigu.agent.harness.knowledge.service import KnowledgeQueryService


@pytest.mark.asyncio
async def test_empty_query_returns_nothing():
    service = KnowledgeQueryService()
    assert await service.search("   ") == []


@pytest.mark.asyncio
async def test_refund_progress_matches_its_own_entry():
    service = KnowledgeQueryService()
    items = await service.search("退款到哪了，什么时候能到账")
    assert items
    assert items[0].knowledge_id.startswith("KB-REFUND")


@pytest.mark.asyncio
async def test_unrelated_query_returns_empty_instead_of_fixed_rules():
    service = KnowledgeQueryService()
    assert await service.search("今天天气怎么样") == []


@pytest.mark.asyncio
async def test_plan_scoped_policy_filters_by_plan():
    service = KnowledgeQueryService()
    contract_items = await service.search("合同审核需要什么材料", plan_id="PLAN_CONTRACT_REVIEW")
    other_items = await service.search("合同审核需要什么材料", plan_id="PLAN_LEGAL_CONSULT")
    assert any(item.knowledge_id == "KB-SERVICE-001" for item in contract_items)
    assert all(item.knowledge_id != "KB-SERVICE-001" for item in other_items)


@pytest.mark.asyncio
async def test_effective_date_filters_out_future_policy():
    service = KnowledgeQueryService()
    items = await service.search("退款条件", effective_at=date(2025, 1, 1))
    assert items == []
