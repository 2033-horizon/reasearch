"""Ticket 02 regression suite: evidence extraction + quote verification.

Covers the four value types, dirty-JSON tolerance, fail-closed quote checks
(with a single correction retry), same-source dedupe, chunking/concurrency
parameters and cost passthrough. LLM is always a local fake; the suite runs
under the repo's network kill-switch.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from gpt_researcher.evidence import EvidenceLayer
from gpt_researcher.evidence.extraction import EvidenceExtractor, chunk_text


class ScriptedLLM:
    """Async fake: delegates to a sync handler, recording every prompt."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    async def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.handler(prompt)


def _payload_item(**overrides):
    item = {
        "entity": "某公司",
        "metric": "新能源汽车销量",
        "value_type": "number",
        "value": "1288.8万",
        "value_raw": "1288.8万辆",
        "unit": "辆",
        "period": {"type": "year", "raw": "2024年"},
        "region": "中国",
        "scope": "全年累计销量",
        "quote": "新能源汽车销量为1288.8万辆",
    }
    item.update(overrides)
    return item


CONTENT = (
    "2024 年，某公司新能源汽车销量为1288.8万辆，同比增长 35.5%。\n"
    "行业渗透率区间为 10%～15%。\n"
    "报告期内公司完成对 A 公司的收购，交易金额约 20 亿元。\n"
    "口径：全年累计销量；期间：2024 年。\n" + "补充正文。" * 40
)


def _scripted(items, corrections=None):
    payload = json.dumps(items, ensure_ascii=False)
    corrections = corrections or []

    def handler(prompt):
        if "证据抽取" in prompt:
            return payload
        return corrections.pop(0) if corrections else '{"quote": ""}'

    return ScriptedLLM(handler)


@pytest.mark.asyncio
async def test_all_four_value_types_are_normalized():
    items = [
        _payload_item(),
        _payload_item(
            metric="行业渗透率",
            value_type="range",
            value="10%～15%",
            value_raw="10%～15%",
            unit="%",
            quote="行业渗透率区间为 10%～15%",
        ),
        _payload_item(
            metric="同比增速",
            value_type="ratio",
            value=35.5,
            value_raw="35.5%",
            unit="%",
            quote="同比增长 35.5%",
        ),
        _payload_item(
            metric="并购事件",
            value_type="text",
            value="某公司完成对A公司的收购",
            value_raw="公司完成对 A 公司的收购",
            unit=None,
            quote="公司完成对 A 公司的收购",
        ),
    ]
    extractor = EvidenceExtractor(llm=_scripted(items), min_content_chars=10)
    evidence, rejected = await extractor.extract("S-001", "https://stats.gov.cn/a", CONTENT)

    assert rejected == []
    by_metric = {item.metric: item for item in evidence}
    assert set(by_metric) == {"新能源汽车销量", "行业渗透率", "同比增速", "并购事件"}

    sales = by_metric["新能源汽车销量"]
    assert sales.value_type == "number"
    assert sales.value == 12888000  # "1288.8万" -> normalized
    assert sales.value_raw == "1288.8万辆"
    assert sales.unit == "辆"
    assert sales.period == {"type": "year", "start": None, "end": None, "raw": "2024年"}
    assert sales.scope == "全年累计销量"
    assert sales.entity == "某公司"
    assert sales.source_id == "S-001"
    assert sales.quote in CONTENT

    assert by_metric["行业渗透率"].value == [10, 15]
    assert by_metric["同比增速"].value == 35.5
    assert by_metric["并购事件"].value_type == "text"


@pytest.mark.asyncio
async def test_quote_variants_whitespace_newline_fullwidth_nfkc_still_match():
    content = (
        "2024 年公司营收为\n1,288.8 亿元，同比增长 35.5％。\n" + "背景材料。" * 40
    )
    variant_quotes = [
        "公司营收为 1,288.8 亿元",  # newline collapsed
        "2024\u3000年公司营收为 1,288.8 亿元",  # ideographic space
        "同比增长 35.5%",  # full-width % normalized
    ]
    items = [
        _payload_item(metric=f"营收{i}", quote=quote, value_type="text", value=quote, value_raw=quote)
        for i, quote in enumerate(variant_quotes)
    ]
    extractor = EvidenceExtractor(llm=_scripted(items), min_content_chars=10)
    evidence, rejected = await extractor.extract("S-001", "https://e.com", content)
    assert rejected == []
    assert len(evidence) == 3


@pytest.mark.asyncio
async def test_fabricated_quote_is_rejected_after_failed_correction():
    items = [_payload_item(quote="公司营收为 2 万亿元")]
    llm = _scripted(items, corrections=['{"quote": ""}'])
    extractor = EvidenceExtractor(llm=llm, min_content_chars=10)
    evidence, rejected = await extractor.extract("S-001", "https://e.com", CONTENT)

    assert evidence == []
    assert len(rejected) == 1
    assert rejected[0].source_id == "S-001"
    assert rejected[0].reason == "quote_not_found"
    assert any("逐字" in call for call in llm.calls), "correction prompt must be attempted"


@pytest.mark.asyncio
async def test_quote_correction_retry_can_rescue_an_item():
    items = [_payload_item(quote="新能源汽车销量1288.8万")]  # slightly off (missing 辆)
    llm = _scripted(items, corrections=['```json\n{"quote": "新能源汽车销量为1288.8万辆"}\n```'])
    extractor = EvidenceExtractor(llm=llm, min_content_chars=10)
    evidence, rejected = await extractor.extract("S-001", "https://e.com", CONTENT)

    assert rejected == []
    assert len(evidence) == 1
    assert evidence[0].quote == "新能源汽车销量为1288.8万辆"


@pytest.mark.asyncio
async def test_dirty_json_tolerance_fenced_prose_and_wrapper():
    item = _payload_item()
    fenced = "```json\n" + json.dumps([item], ensure_ascii=False) + "\n```"
    prose = "好的，以下是抽取结果：" + json.dumps([item], ensure_ascii=False)
    wrapper = json.dumps({"items": [item]}, ensure_ascii=False)
    for response in (fenced, prose, wrapper):
        extractor = EvidenceExtractor(llm=_scripted_raw(response), min_content_chars=10)
        evidence, rejected = await extractor.extract("S-001", "https://e.com", CONTENT)
        assert len(evidence) == 1, response
        assert rejected == []


def _scripted_raw(response):
    return ScriptedLLM(lambda prompt: response)


@pytest.mark.asyncio
async def test_unparseable_response_goes_to_rejected():
    extractor = EvidenceExtractor(
        llm=_scripted_raw("抱歉，我无法完成这个请求。没有 JSON。"),
        min_content_chars=10,
    )
    evidence, rejected = await extractor.extract("S-007", "https://e.com", CONTENT)
    assert evidence == []
    assert rejected[0].source_id == "S-007"
    assert rejected[0].reason == "parse_failed"
    assert rejected[0].detail


@pytest.mark.asyncio
async def test_missing_metric_skipped_and_missing_quote_rejected():
    items = [
        {"value_type": "text", "value": "没有指标", "quote": "补充正文"},
        _payload_item(quote=""),
    ]
    extractor = EvidenceExtractor(llm=_scripted(items), min_content_chars=10)
    evidence, rejected = await extractor.extract("S-001", "https://e.com", CONTENT)
    assert evidence == []
    assert len(rejected) == 1
    assert rejected[0].reason == "quote_not_found"
    assert rejected[0].detail == "quote_missing"


@pytest.mark.asyncio
async def test_same_source_dedupe_across_chunks_and_cross_source_duplicates_kept():
    repeated_item = _payload_item(quote="销量为 100 万辆", value_raw="100 万辆")
    repeated = "第一段：销量为 100 万辆。" * 10
    extractor = EvidenceExtractor(
        llm=_scripted([repeated_item, repeated_item]),
        min_content_chars=10,
        chunk_size=48,
        chunk_overlap=16,
        concurrency=1,
    )
    evidence, _ = await extractor.extract("S-001", "https://e.com", repeated)
    assert len(extractor.llm.calls) > 1, "content must span multiple chunks"
    assert len(evidence) == 1, "duplicate metric+value+period within a source must collapse"

    # Cross-source duplicates stay separate at the layer level.
    layer = EvidenceLayer(
        config=SimpleNamespace(
            evidence_llm="fast",
            evidence_max_sources=10,
            evidence_max_chars_per_source=30000,
            evidence_chunk_size=8000,
            evidence_chunk_overlap=400,
            evidence_concurrency=4,
            tier_cache_path="",
        ),
        llm=_scripted([_payload_item()]),
    )
    sources = [
        {"url": "https://stats.gov.cn/a", "title": "", "raw_content": CONTENT},
        {"url": "https://people.com.cn/b", "title": "", "raw_content": CONTENT},
    ]
    artifact = await layer.build(sources, research_id="r1", query="q")
    assert len(artifact.evidence) == 2
    assert {e.source_id for e in artifact.evidence} == {"S-001", "S-002"}
    assert [e.id for e in artifact.evidence] == ["E-001", "E-002"]


@pytest.mark.asyncio
async def test_short_or_missing_content_is_not_extracted():
    llm = _scripted([_payload_item()])
    extractor = EvidenceExtractor(llm=llm)  # default 300-char threshold
    evidence, rejected = await extractor.extract("S-001", "https://e.com", "太短了。")
    assert (evidence, rejected, llm.calls) == ([], [], [])
    evidence, rejected = await extractor.extract("S-001", "https://e.com", "")
    assert (evidence, rejected, llm.calls) == ([], [], [])


@pytest.mark.asyncio
async def test_truncation_chunks_and_configurable_parameters():
    text = "开头内容。" * 30 + "关键结论：销量为 999 万辆。" + "结尾内容。" * 30
    extractor = EvidenceExtractor(
        llm=_scripted([_payload_item(quote="销量为 999 万辆")]),
        min_content_chars=10,
        max_chars=100,
        chunk_size=40,
        chunk_overlap=10,
    )
    evidence, rejected = await extractor.extract("S-001", "https://e.com", text)

    # The only quote lives past the 100-char truncation window -> fail closed.
    assert evidence == []
    assert rejected[0].reason == "quote_not_found"
    assert all("关键结论" not in call for call in extractor.llm.calls)

    # Chunk math: 100 chars / (40 size - 10 overlap) -> 3 chunks.
    assert len(chunk_text("x" * 100, 40, 10)) == 3


@pytest.mark.asyncio
async def test_concurrency_limit_is_respected():
    inflight = 0
    peak = 0

    class SlowLLM:
        async def __call__(self, prompt):
            nonlocal inflight, peak
            inflight += 1
            peak = max(peak, inflight)
            await asyncio.sleep(0.01)
            inflight -= 1
            return "[]"

    extractor = EvidenceExtractor(
        llm=SlowLLM(),
        min_content_chars=10,
        chunk_size=40,
        chunk_overlap=0,
        concurrency=2,
    )
    await extractor.extract("S-001", "https://e.com", "y" * 200)
    assert peak <= 2
    assert peak >= 2, "chunks should actually run concurrently"


@pytest.mark.asyncio
async def test_extraction_llm_cost_is_reported_to_existing_cost_tracking(monkeypatch):
    charged = []

    async def fake_create_chat_completion(*args, **kwargs):
        kwargs["cost_callback"](0.002)
        return "[]"

    monkeypatch.setattr(
        "gpt_researcher.evidence.layer.create_chat_completion",
        fake_create_chat_completion,
    )
    layer = EvidenceLayer(
        config=SimpleNamespace(
            evidence_llm="fast",
            fast_llm_provider="openai",
            fast_llm_model="gpt-test",
            fast_token_limit=1000,
            llm_kwargs={},
            evidence_max_sources=10,
            evidence_max_chars_per_source=30000,
            evidence_chunk_size=8000,
            evidence_chunk_overlap=400,
            evidence_concurrency=4,
            tier_cache_path="",
        ),
        cost_callback=charged.append,
    )
    await layer.build(
        [{"url": "https://stats.gov.cn/a", "title": "", "raw_content": CONTENT}],
        research_id="r1",
        query="q",
    )
    assert charged == [0.002]
