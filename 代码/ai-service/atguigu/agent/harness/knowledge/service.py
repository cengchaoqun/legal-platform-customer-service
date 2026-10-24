import json
import unicodedata
from collections.abc import Iterable
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from atguigu.agent.harness.knowledge.output import KnowledgeItem

# 首版政策条目随版本发布，离线维护；不把 RAG 上传能力纳入当前承诺。
POLICY_FILE = Path(__file__).resolve().parent / "policies" / "legal-support-policies.json"


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip().casefold()


@lru_cache(maxsize=1)
def load_policy_entries(path: str | None = None) -> tuple[dict[str, Any], ...]:
    """读取并缓存政策条目；文件缺失时返回空集合而不是示例政策。"""
    target = Path(path) if path else POLICY_FILE
    if not target.exists():
        return ()
    with target.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, list):
        raise ValueError("政策文件必须是条目数组")
    return tuple(raw)


class KnowledgeQueryService:
    """版本化服务政策检索。

    首版使用关键词与主题匹配：没有命中返回空结果，不返回固定示例；
    条目带版本、适用套餐与生效区间，避免把一条规则套给所有套餐。
    """

    def __init__(self, policy_path: str | None = None):
        self.entries = load_policy_entries(policy_path)
        self.catalog_version = (
            str(self.entries[0].get("version")) if self.entries else "policy-empty"
        )

    async def search(
        self,
        query: str,
        plan_id: str | None = None,
        effective_at: date | None = None,
    ) -> list[KnowledgeItem]:
        normalized_query = _normalize(query or "")
        if not normalized_query:
            return []

        today = effective_at or date.today()
        scored: list[tuple[int, KnowledgeItem]] = []
        for entry in self.entries:
            if not self._is_effective(entry, today):
                continue
            applicable = entry.get("applicable_plans") or []
            if plan_id and applicable and plan_id not in applicable:
                continue
            score = self._score(entry, normalized_query)
            if score <= 0:
                continue
            scored.append((score, self._to_item(entry)))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:3]]

    @staticmethod
    def _is_effective(entry: dict[str, Any], today: date) -> bool:
        if entry.get("status", "ACTIVE") != "ACTIVE":
            return False
        start = entry.get("effective_from")
        end = entry.get("effective_to")
        if start and date.fromisoformat(start) > today:
            return False
        if end and date.fromisoformat(end) < today:
            return False
        return True

    @staticmethod
    def _score(entry: dict[str, Any], query: str) -> int:
        keywords: Iterable[str] = entry.get("keywords") or []
        score = 0
        for keyword in keywords:
            normalized = _normalize(keyword)
            if normalized and normalized in query:
                # 更长的关键词更具区分度
                score += len(normalized)
        title = _normalize(str(entry.get("title", "")))
        if title and title in query:
            score += 5
        return score

    @staticmethod
    def _to_item(entry: dict[str, Any]) -> KnowledgeItem:
        return KnowledgeItem(
            knowledge_id=str(entry["knowledge_id"]),
            title=str(entry["title"]),
            content=str(entry["content"]),
            category=str(entry.get("category", "POLICY")),
            version=str(entry.get("version", "policy-v1")),
            effective_from=(
                date.fromisoformat(entry["effective_from"])
                if entry.get("effective_from")
                else None
            ),
            effective_to=(
                date.fromisoformat(entry["effective_to"])
                if entry.get("effective_to")
                else None
            ),
            applicable_plans=list(entry.get("applicable_plans") or []),
            source=str(entry.get("source", "平台政策库")),
            status=str(entry.get("status", "ACTIVE")),
        )
