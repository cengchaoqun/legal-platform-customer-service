import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from atguigu.agent.harness.tools.snapshot import ToolCallSnapshot


class FactCategory(StrEnum):
    IDENTIFIER = "IDENTIFIER"
    NUMBER = "NUMBER"
    STATUS = "STATUS"


# 状态中文标签 -> 业务编码。只保留法律平台实际使用的状态。
STATUS_CODE_MAP = {
    # 支付
    "待支付": "UNPAID",
    "已支付": "PAID",
    "已退款": "REFUNDED",
    # 履约
    "待开始": "PENDING_START",
    "服务中": "IN_SERVICE",
    "材料待补充": "WAITING_MATERIALS",
    "已交付": "DELIVERED",
    "已完成": "COMPLETED",
    "已取消": "CANCELLED",
    # 开票
    "尚未申请开票": "NONE",
    "开票申请已受理": "SUBMITTED",
    "开票处理中": "PROCESSING",
    "发票已开具": "ISSUED",
    "开票失败": "FAILED",
    # 售后申请
    "申请已提交": "SUBMITTED",
    "审核中": "REVIEWING",
    "审核通过": "APPROVED",
    "审核未通过": "REJECTED",
    # 退款资金
    "尚未退款": "NOT_REFUNDED",
    "退款处理中": "REFUNDING",
}

# 明示“已经替用户完成写操作”的表述：AI 没有写权限，出现即视为违规
COMMITMENT_PATTERNS = (
    re.compile(r"已为你(提交|取消|申请|办理|开票|退款)"),
    re.compile(r"已经(帮你|为你)(提交|取消|申请|办理|开票|退款)"),
    re.compile(r"(退款|款项)已(经)?退回"),
    re.compile(r"(退款|款项)(已经|已)到账"),
    re.compile(r"已经(帮你|为你)转(接)?人工"),
)


@dataclass(frozen=True)
class BusinessFact:
    """从 Agent 回复中提取的一项业务事实。"""

    category: FactCategory
    value: str


class FactExtractor:
    """从 Agent 回复中提取需要业务证据支持的事实。"""

    # 订单、套餐、售后与发票编号
    resource_id_pattern = re.compile(
        r"\b(?:ORDER|PLAN|AS|INV)[_-][A-Z0-9_-]+\b",
        re.IGNORECASE,
    )
    # 带金额标签的数字
    labeled_number_pattern = re.compile(
        r"(?:金额|总价|价格|实付|申请金额|退款金额)"
        r"(?:为|是|：|:)?\s*[¥￥]?\s*(\d+(?:\.\d+)?)"
    )
    # 货币符号或“元”结尾的数字
    unit_number_pattern = re.compile(
        r"(?:[¥￥]\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*元)"
    )
    status_labels = tuple(STATUS_CODE_MAP)

    @classmethod
    def extract(cls, reply_content: str) -> tuple[BusinessFact, ...]:
        facts: dict[BusinessFact, None] = {}

        for resource_id in cls.resource_id_pattern.findall(reply_content):
            normalized = resource_id.upper().replace("-", "_")
            facts[BusinessFact(FactCategory.IDENTIFIER, normalized)] = None

        for number in cls.labeled_number_pattern.findall(reply_content):
            facts[BusinessFact(FactCategory.NUMBER, number)] = None

        for first_number, second_number in cls.unit_number_pattern.findall(reply_content):
            facts[BusinessFact(FactCategory.NUMBER, first_number or second_number)] = None

        for label in cls.status_labels:
            for match in re.finditer(re.escape(label), reply_content):
                start, end = match.span()
                prefix = reply_content[max(0, start - 3):start]
                suffix = reply_content[end:end + 1]
                # 条件句与否定句不是事实断言，例如“审核通过后才会退款”
                if any(word in prefix for word in ("如果", "需要", "须", "待", "未", "不")):
                    continue
                if suffix in ("后", "前", "与", "和", "、"):
                    continue
                facts[BusinessFact(FactCategory.STATUS, label)] = None
                break

        return tuple(facts)

    @classmethod
    def find_commitment_claims(cls, reply_content: str) -> tuple[str, ...]:
        """返回没有写权限却声称已完成的表述。"""
        claims: list[str] = []
        for pattern in COMMITMENT_PATTERNS:
            for claim in pattern.finditer(reply_content):
                # 仅排除紧邻的明确否定；逐项检查，避免前一句否定掩盖后一句承诺。
                prefix = reply_content[max(0, claim.start() - 16):claim.start()]
                if re.search(r'(?:不等于|不代表|并不表示|并非|不能确认|无法确认|不能承诺)[“\"‘\s]*$', prefix):
                    continue
                claims.append(claim.group(0))
        return tuple(claims)


class FactChecker:
    """匹配回复事实与成功工具数据。"""

    number_pattern = re.compile(r"^[+-]?\d+(?:\.\d+)?$")

    def get_unsupported_facts(
        self,
        reply_content: str,
        successful_tool_calls: tuple[ToolCallSnapshot, ...],
        *,
        include_status: bool = True,
    ) -> tuple[BusinessFact, ...]:
        facts = FactExtractor.extract(reply_content)

        # 政策类回答没有业务数据，状态标签不是业务断言，跳过状态校验
        if not include_status:
            facts = tuple(
                fact for fact in facts if fact.category != FactCategory.STATUS
            )

        # 没有成功业务工具时豁免数值事实，但仍校验编号与状态
        if not successful_tool_calls:
            facts = tuple(
                fact for fact in facts if fact.category != FactCategory.NUMBER
            )

        if not facts:
            return ()

        source_decimals: set[Decimal] = set()
        normalized_sources: set[str] = set()

        for tool_call in successful_tool_calls:
            data = (tool_call.result or {}).get("data")
            if tool_call.tool_name == "list_orders" and isinstance(data, list):
                source_decimals.add(Decimal(len(data)))

            for source in (tool_call.arguments, data):
                for scalar in self._iter_scalars(source):
                    normalized_sources.add(self._normalize_text(scalar))
                    decimal_value = self._to_decimal(scalar)
                    if decimal_value is not None:
                        source_decimals.add(decimal_value)

        return tuple(
            fact
            for fact in facts
            if not self._is_supported(fact, source_decimals, normalized_sources)
        )

    @classmethod
    def _is_supported(
        cls,
        fact: BusinessFact,
        source_decimals: set[Decimal],
        normalized_sources: set[str],
    ) -> bool:
        if fact.category == FactCategory.NUMBER:
            return Decimal(fact.value) in source_decimals

        normalized_value = cls._normalize_text(fact.value)

        if fact.category == FactCategory.STATUS:
            code = STATUS_CODE_MAP[fact.value]
            return normalized_value in normalized_sources or cls._normalize_text(code) in normalized_sources

        return normalized_value in normalized_sources

    @classmethod
    def _to_decimal(cls, value: object) -> Decimal | None:
        text = str(value).strip()
        if cls.number_pattern.fullmatch(text) is None:
            return None
        return Decimal(text)

    @staticmethod
    def _normalize_text(value: object) -> str:
        return (
            unicodedata.normalize("NFKC", str(value))
            .strip()
            .casefold()
            .replace("-", "_")
        )

    @classmethod
    def _iter_scalars(cls, value: object) -> Iterable[object]:
        if isinstance(value, dict):
            for nested_value in value.values():
                yield from cls._iter_scalars(nested_value)
        elif isinstance(value, list):
            for nested_value in value:
                yield from cls._iter_scalars(nested_value)
        else:
            yield value
