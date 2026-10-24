"""否定性政策说明不能误判为退款承诺，后续真实承诺仍须拦截。"""
import pytest
from atguigu.agent.harness.rules.fact import FactExtractor, FactChecker


@pytest.mark.parametrize("text", ["审核通过不等于“退款已到账”。", "无法确认退款已到账。"])
def test_explicit_negation_is_not_a_completed_refund(text):
    assert FactExtractor.find_commitment_claims(text) == ()


def test_negation_does_not_hide_later_positive_claim():
    assert FactExtractor.find_commitment_claims("审核通过不等于“退款已到账”。不过你的退款已到账。") == ("退款已到账",)


def test_positive_refund_claim_is_still_rejected():
    assert FactExtractor.find_commitment_claims("你的退款已到账。") == ("退款已到账",)


def test_already_approved_requires_business_evidence():
    assert FactChecker().get_unsupported_facts("您的售后申请已经审核通过。", ())
