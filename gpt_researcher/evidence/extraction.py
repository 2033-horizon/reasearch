"""Evidence extraction: chunked LLM extraction with fail-closed quote checks.

Per ADR-0003 every extracted item must carry a verbatim quote from the
source; quotes are NFKC-normalized + whitespace-folded and matched strictly.
A failed quote gets one LLM correction attempt, then the item is dropped
into ``rejected[]`` (no fuzzy matching, no unverified items downstream).
"""

import asyncio
import json
import logging
import re
import unicodedata
from typing import Any, Awaitable, Callable

from .models import EvidenceItem, RejectedItem
from .parsing import iter_parsed_candidates

logger = logging.getLogger(__name__)

MIN_CONTENT_CHARS = 300
LLMCall = Callable[[str], Awaitable[str]]

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_RANGE_SPLIT_RE = re.compile(r"[-~～–—至]")
_CN_MULTIPLIERS = {"亿": 1e8, "万": 1e4, "千": 1e3, "百": 1e2}


def build_extraction_prompt(url: str, chunk: str) -> str:
    """Prompt for the evidence extraction step (marker: 证据抽取)."""
    return (
        "你是市场调研的证据抽取助手（证据抽取）。从下面的网页正文中抽取可溯源的"
        "数据点或文本型事实。只抽取正文中明确出现的内容，不要推理、不要编造。\n"
        "每条证据输出一个 JSON 对象，字段：\n"
        '{"entity": "指标所属主体（企业/行业/地区，可为空）", "metric": "指标名", '
        '"value_type": "number|range|ratio|text", "value": 归一化数值或文本, '
        '"value_raw": "原文中的写法", "unit": "单位（可为空）", '
        '"period": {"type": "year|quarter|month|range|point|unknown", "start": "", "end": "", "raw": "原文期间写法"}, '
        '"region": "地域（可为空）", "scope": "统计口径（可为空）", '
        '"quote": "正文中原样摘录的句子，必须逐字出现，不能改写"}\n'
        "返回 JSON 数组；没有可抽取内容时返回 []。\n\n"
        f"来源：{url}\n"
        f"网页正文：\n{chunk}"
    )


def build_quote_correction_prompt(quote: str, chunk: str) -> str:
    return (
        "下面这条引用未能在网页正文中逐字找到：\n"
        f"{quote}\n\n"
        "请在网页正文中找出对应事实的逐字原文（不超过 200 字）。"
        "只输出 JSON：{\"quote\": \"逐字原文\"}；如果正文中确实没有该事实，输出 {\"quote\": \"\"}。\n\n"
        f"网页正文：\n{chunk}"
    )


def fold_text(text: str) -> str:
    """NFKC-normalize and collapse whitespace runs for strict matching."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "")).strip()


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    if size <= 0 or len(text) <= size:
        return [text] if text.strip() else []
    step = max(1, size - max(0, overlap))
    chunks = []
    for start in range(0, len(text), step):
        chunk = text[start : start + size]
        if chunk.strip():
            chunks.append(chunk)
        if start + size >= len(text):
            break
    return chunks


def _clean_number(number: float) -> int | float:
    return int(number) if float(number).is_integer() else number


def _to_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return _clean_number(value)
    if not isinstance(value, str):
        return None
    text = unicodedata.normalize("NFKC", value).strip().replace(",", "").replace(" ", "")
    match = _NUMBER_RE.search(text)
    if not match:
        return None
    number = float(match.group(0))
    remainder = text[match.end() :].lstrip()
    for unit, multiplier in _CN_MULTIPLIERS.items():
        if remainder.startswith(unit):
            number *= multiplier
            break
    return _clean_number(number)


def _normalize_value(value_type: str, raw_value: Any) -> Any:
    if value_type in {"number", "ratio"}:
        return _to_number(raw_value)
    if value_type == "range":
        if isinstance(raw_value, (list, tuple)):
            numbers = [_to_number(part) for part in raw_value[:2]]
            return [n for n in numbers if n is not None]
        if isinstance(raw_value, str):
            parts = [part for part in _RANGE_SPLIT_RE.split(raw_value) if part.strip()]
            numbers = [_to_number(part) for part in parts]
            numbers = [n for n in numbers if n is not None]
            return numbers
        return None
    return str(raw_value) if raw_value is not None else ""


def parse_items_payload(response: str) -> list[dict] | None:
    """Recover a list of item dicts from dirty LLM output; None on failure."""
    for parsed in iter_parsed_candidates(response):
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        if isinstance(parsed, dict):
            for key in ("items", "evidence", "data", "records"):
                value = parsed.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
            if parsed.get("metric"):
                return [parsed]
    return None


class EvidenceExtractor:
    """Extracts verified evidence items from one source's content."""

    def __init__(
        self,
        llm: LLMCall | None,
        *,
        max_chars: int = 30000,
        chunk_size: int = 8000,
        chunk_overlap: int = 400,
        concurrency: int = 4,
        extractor_name: str = "unknown",
        min_content_chars: int = MIN_CONTENT_CHARS,
    ):
        self.llm = llm
        self.max_chars = max_chars
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.concurrency = max(1, concurrency)
        self.extractor_name = extractor_name
        self.min_content_chars = min_content_chars

    async def extract(
        self,
        source_id: str,
        url: str,
        content: str,
        semaphore: asyncio.Semaphore | None = None,
    ) -> tuple[list[EvidenceItem], list[RejectedItem]]:
        if self.llm is None:
            return [], []
        content = content or ""
        if len(content.strip()) < self.min_content_chars:
            return [], []

        truncated = content[: self.max_chars] if self.max_chars > 0 else content
        chunks = chunk_text(truncated, self.chunk_size, self.chunk_overlap)
        if not chunks:
            return [], []

        # A shared semaphore (passed by the layer) bounds LLM calls across all
        # sources, not just within one source, so cost stays predictable.
        gate = semaphore if semaphore is not None else asyncio.Semaphore(self.concurrency)

        async def process(chunk: str) -> tuple[list[EvidenceItem], list[RejectedItem]]:
            async with gate:
                return await self._extract_chunk(source_id, url, chunk)

        results = await asyncio.gather(*(process(chunk) for chunk in chunks))

        items: list[EvidenceItem] = []
        rejected: list[RejectedItem] = []
        for chunk_items, chunk_rejected in results:
            items.extend(chunk_items)
            rejected.extend(chunk_rejected)

        deduped: list[EvidenceItem] = []
        seen: set[tuple] = set()
        for item in items:
            key = (
                item.metric.strip().lower(),
                json.dumps(item.value, ensure_ascii=False, sort_keys=True),
                json.dumps(item.period or {}, ensure_ascii=False, sort_keys=True),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped, rejected

    async def _extract_chunk(
        self,
        source_id: str,
        url: str,
        chunk: str,
    ) -> tuple[list[EvidenceItem], list[RejectedItem]]:
        try:
            response = await self.llm(build_extraction_prompt(url, chunk))
        except Exception as exc:
            logger.warning("Evidence extraction LLM call failed for %s: %s", url, exc)
            return [], [RejectedItem(source_id=source_id, reason="parse_failed", detail=str(exc))]

        payload = parse_items_payload(response)
        if payload is None:
            return [], [
                RejectedItem(
                    source_id=source_id,
                    reason="parse_failed",
                    detail=(response or "")[:200],
                )
            ]

        items: list[EvidenceItem] = []
        rejected: list[RejectedItem] = []
        for raw_item in payload:
            item = self._normalize_item(source_id, raw_item)
            if item is None:
                continue
            if not self._quote_found(item.quote, chunk):
                corrected = await self._correct_quote(item.quote, chunk)
                if corrected:
                    item.quote = corrected
                else:
                    rejected.append(
                        RejectedItem(
                            source_id=source_id,
                            reason="quote_not_found",
                            detail=item.quote or "quote_missing",
                        )
                    )
                    continue
            items.append(item)
        return items, rejected

    def _normalize_item(self, source_id: str, raw_item: dict) -> EvidenceItem | None:
        metric = str(raw_item.get("metric") or "").strip()
        if not metric:
            return None
        raw_value = raw_item.get("value")
        value_type = str(raw_item.get("value_type") or "").strip().lower()
        if value_type not in {"number", "range", "ratio", "text"}:
            if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
                value_type = "number"
            else:
                value_type = "text"
        value = _normalize_value(value_type, raw_value)
        if value is None:
            return None

        period = raw_item.get("period")
        if isinstance(period, str):
            period = {"type": "unknown", "raw": period}
        elif isinstance(period, dict):
            period = {
                "type": str(period.get("type") or "unknown"),
                "start": period.get("start"),
                "end": period.get("end"),
                "raw": str(period.get("raw") or ""),
            }
        else:
            period = None

        value_raw = raw_item.get("value_raw")
        if value_raw is None:
            value_raw = raw_value if isinstance(raw_value, str) else json.dumps(
                raw_value, ensure_ascii=False
            )
        return EvidenceItem(
            id="",
            source_id=source_id,
            metric=metric,
            value_type=value_type,
            value=value,
            value_raw=str(value_raw),
            quote=str(raw_item.get("quote") or "").strip(),
            extractor=self.extractor_name,
            entity=str(raw_item.get("entity") or "").strip() or None,
            unit=str(raw_item.get("unit") or "").strip() or None,
            period=period,
            region=str(raw_item.get("region") or "").strip() or None,
            scope=str(raw_item.get("scope") or "").strip() or None,
        )

    def _quote_found(self, quote: str, content: str) -> bool:
        folded = fold_text(quote)
        return bool(folded) and folded in fold_text(content)

    async def _correct_quote(self, quote: str, chunk: str) -> str | None:
        if not quote:
            return None
        try:
            response = await self.llm(build_quote_correction_prompt(quote, chunk))
        except Exception as exc:
            logger.warning("Quote correction LLM call failed: %s", exc)
            return None
        parsed = next(
            (
                candidate
                for candidate in iter_parsed_candidates(response)
                if isinstance(candidate, dict)
            ),
            None,
        )
        if not isinstance(parsed, dict):
            return None
        candidate = str(parsed.get("quote") or "").strip()
        if candidate and self._quote_found(candidate, chunk):
            return candidate
        return None
