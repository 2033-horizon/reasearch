"""Ticket 07 regression suite: clustering + adjudication baseline.

Exercises the adjudication pipeline through its public entry
(``Adjudicator.adjudicate`` / ``EvidenceLayer.build``) with hand-built
evidence, asserting group membership, verdicts, representative values and the
schema v2 artifact contract.
"""

import json
from types import SimpleNamespace

import pytest

from gpt_researcher.evidence import (
    AdjudicationRules,
    Adjudicator,
    EvidenceLayer,
    EvidenceItem,
    SourceProfile,
)


def _source(source_id, domain, tier, publisher=None):
    return SourceProfile(
        id=source_id,
        url=f"https://{domain}/article",
        domain=domain,
        tier=tier,
        assigned_by="rule",
        publisher=publisher,
        scraped=True,
    )


_QUOTES = [
    "该季度市场整体保持平稳，未出现明显波动。",
    "行业分析师指出需求端正在逐步回暖。",
    "统计公报显示产业结构持续优化升级。",
    "多家企业反馈订单量同比增长明显。",
    "权威部门预计下阶段将延续恢复态势。",
    "区域市场表现分化，头部企业优势扩大。",
]


def _item(
    item_id,
    source_id,
    *,
    metric="新能源汽车销量",
    value=100,
    value_type="number",
    value_raw=None,
    scope="全年累计",
    entity="新能源汽车",
    unit="辆",
    period_raw="2024年",
    extracted_at="2026-09-29T00:00:00+00:00",
    quote=None,
):
    return EvidenceItem(
        id=item_id,
        source_id=source_id,
        metric=metric,
        value_type=value_type,
        value=value,
        value_raw=value_raw if value_raw is not None else str(value),
        quote=quote
        if quote is not None
        else _QUOTES[(int(item_id.split("-")[-1]) - 1) % len(_QUOTES)],
        extractor="fake:model",
        entity=entity,
        unit=unit,
        period={"type": "year", "raw": period_raw},
        scope=scope,
        extracted_at=extracted_at,
    )


async def _adjudicate(items, sources, rules=None):
    adjudicator = Adjudicator(rules or AdjudicationRules.load())
    return await adjudicator.adjudicate(items, sources)


RULES_YAML = """version: 1
tiers:
  A: { min_independent_sources: 1 }
  B: { min_independent_sources: 2 }
  C: { min_independent_sources: 2 }
  D: { min_independent_sources: 3 }
value_tolerance:
  relative: 0.05
resolution_order: [tier, recency]
"""


def _write_rules(tmp_path, content=RULES_YAML):
    path = tmp_path / "adjudication.yaml"
    path.write_text(content, encoding="utf-8")
    return str(path)


# --------------------------------------------------------------------- rules


def test_packaged_rules_load_with_defaults():
    rules = AdjudicationRules.load()
    assert rules.thresholds == {"A": 1, "B": 1, "C": 2, "D": 3}
    assert rules.tolerance == pytest.approx(0.05)
    assert rules.resolution_order == ("tier", "recency")


def test_custom_rules_path_overrides_thresholds(tmp_path):
    rules = AdjudicationRules.load(_write_rules(tmp_path))
    assert rules.thresholds["B"] == 2
    assert rules.thresholds["A"] == 1


def test_missing_custom_rules_fall_back_to_packaged(tmp_path):
    rules = AdjudicationRules.load(str(tmp_path / "nope.yaml"))
    assert rules.thresholds == {"A": 1, "B": 1, "C": 2, "D": 3}


def test_malformed_rules_fall_back_to_defaults(tmp_path):
    rules = AdjudicationRules.load(_write_rules(tmp_path, "tiers: [broken"))
    assert rules.thresholds == {"A": 1, "B": 1, "C": 2, "D": 3}
    assert rules.tolerance == pytest.approx(0.05)


def test_invalid_tolerance_and_order_fall_back(tmp_path):
    rules = AdjudicationRules.load(
        _write_rules(
            tmp_path,
            "version: 1\ntiers: {}\nvalue_tolerance: {relative: 2.5}\nresolution_order: [nonsense]\n",
        )
    )
    assert rules.tolerance == pytest.approx(0.05)
    assert rules.resolution_order == ("tier", "recency")


# ---------------------------------------------------------------- clustering


@pytest.mark.asyncio
async def test_same_metric_and_scope_merge_across_sources():
    sources = [_source("S-001", "a.example", "C"), _source("S-002", "b.example", "C")]
    items = [_item("E-001", "S-001"), _item("E-002", "S-002")]

    groups = await _adjudicate(items, sources)

    assert len(groups) == 1
    group = groups[0]
    assert group.members == ["E-001", "E-002"]
    assert group.source_ids == ["S-001", "S-002"]
    assert group.independent_sources == 2
    assert group.verdict["status"] == "cross_validated"


@pytest.mark.asyncio
async def test_different_scope_text_splits_groups():
    sources = [_source("S-001", "a.example", "C"), _source("S-002", "b.example", "C")]
    items = [
        _item("E-001", "S-001", scope="全年累计销量"),
        _item("E-002", "S-002", scope="出口销量"),
    ]

    groups = await _adjudicate(items, sources)

    assert len(groups) == 2
    assert {group.scope for group in groups} == {"全年累计销量", "出口销量"}


@pytest.mark.asyncio
async def test_missing_scopes_merge_and_different_metric_splits():
    sources = [_source("S-001", "a.example", "A"), _source("S-002", "b.example", "A")]
    items = [
        _item("E-001", "S-001", scope=None, metric="销量"),
        _item("E-002", "S-002", scope=None, metric="产量"),
    ]

    groups = await _adjudicate(items, sources)

    assert len(groups) == 2
    assert {group.key["metric"] for group in groups} == {"销量", "产量"}


@pytest.mark.asyncio
async def test_entity_period_region_unit_blocking():
    sources = [_source("S-001", "a.example", "A"), _source("S-002", "b.example", "A")]
    items = [
        _item("E-001", "S-001"),
        _item("E-002", "S-002", entity="充电桩"),
    ]

    groups = await _adjudicate(items, sources)

    assert len(groups) == 2


# ------------------------------------------------------------------ verdicts


@pytest.mark.asyncio
async def test_a_tier_single_source_is_accepted():
    sources = [_source("S-001", "stats.gov.cn", "A")]
    groups = await _adjudicate([_item("E-001", "S-001")], sources)

    assert groups[0].verdict["status"] == "accepted"
    assert groups[0].verdict["rule"] == "tier_minimum:A=1"


@pytest.mark.asyncio
async def test_b_tier_single_source_is_accepted_by_default():
    sources = [_source("S-001", "people.com.cn", "B")]
    groups = await _adjudicate([_item("E-001", "S-001")], sources)

    assert groups[0].verdict["status"] == "accepted"


@pytest.mark.asyncio
async def test_b_threshold_is_configurable(tmp_path):
    rules = AdjudicationRules.load(_write_rules(tmp_path))
    sources = [_source("S-001", "people.com.cn", "B")]
    groups = await _adjudicate([_item("E-001", "S-001")], sources, rules=rules)

    assert groups[0].verdict["status"] == "insufficient_pending"
    assert groups[0].verdict["required"] == 2


@pytest.mark.asyncio
async def test_c_tier_requires_two_independent_sources():
    one_source = [_source("S-001", "a.example", "C")]
    groups = await _adjudicate([_item("E-001", "S-001")], one_source)
    assert groups[0].verdict["status"] == "insufficient_pending"

    two_sources = [_source("S-001", "a.example", "C"), _source("S-002", "b.example", "C")]
    groups = await _adjudicate(
        [_item("E-001", "S-001"), _item("E-002", "S-002")], two_sources
    )
    assert groups[0].verdict["status"] == "cross_validated"


@pytest.mark.asyncio
async def test_d_tier_requires_three_independent_sources():
    sources = [_source(f"S-{i:03d}", f"blog{i}.example", "D") for i in (1, 2)]
    groups = await _adjudicate(
        [_item("E-001", "S-001"), _item("E-002", "S-002")], sources
    )
    assert groups[0].verdict["status"] == "insufficient_pending"

    sources.append(_source("S-003", "blog3.example", "D"))
    groups = await _adjudicate(
        [
            _item("E-001", "S-001"),
            _item("E-002", "S-002"),
            _item("E-003", "S-003"),
        ],
        sources,
    )
    assert groups[0].verdict["status"] == "cross_validated"


@pytest.mark.asyncio
async def test_same_domain_counts_once():
    sources = [_source("S-001", "blog.example", "C"), _source("S-002", "blog.example", "C")]
    items = [_item("E-001", "S-001"), _item("E-002", "S-002", value_raw="101")]

    groups = await _adjudicate(items, sources)

    assert groups[0].independent_sources == 1
    assert groups[0].verdict["status"] == "insufficient_pending"


@pytest.mark.asyncio
async def test_tolerance_absorbs_small_numeric_differences():
    sources = [_source("S-001", "a.example", "C"), _source("S-002", "b.example", "C")]
    items = [
        _item("E-001", "S-001", value=100, value_raw="100万辆"),
        _item("E-002", "S-002", value=104, value_raw="104万辆"),
    ]

    groups = await _adjudicate(items, sources)

    assert len(groups[0].verdict["conflicts"]) == 0
    assert groups[0].verdict["status"] == "cross_validated"


@pytest.mark.asyncio
async def test_conflict_resolved_by_tier():
    sources = [_source("S-001", "stats.gov.cn", "A"), _source("S-002", "blog.example", "D")]
    items = [
        _item("E-001", "S-001", value=100),
        _item("E-002", "S-002", value=200),
    ]

    groups = await _adjudicate(items, sources)

    group = groups[0]
    assert group.verdict["resolution"] == "tier"
    assert group.verdict["status"] == "accepted"
    assert group.representative["evidence_id"] == "E-001"
    assert group.verdict["conflicts"][0]["members"] == ["E-002"]


@pytest.mark.asyncio
async def test_conflict_resolved_by_recency_when_tiers_tie():
    sources = [_source("S-001", "a.example", "A"), _source("S-002", "b.example", "A")]
    items = [
        _item("E-001", "S-001", value=100, extracted_at="2026-09-01T00:00:00+00:00"),
        _item("E-002", "S-002", value=200, extracted_at="2026-09-20T00:00:00+00:00"),
    ]

    groups = await _adjudicate(items, sources)

    group = groups[0]
    assert group.verdict["resolution"] == "recency"
    assert group.representative["evidence_id"] == "E-002"


@pytest.mark.asyncio
async def test_irreconcilable_conflict_is_pending():
    sources = [_source("S-001", "a.example", "A"), _source("S-002", "b.example", "A")]
    items = [
        _item("E-001", "S-001", value=100),
        _item("E-002", "S-002", value=200),
    ]

    groups = await _adjudicate(items, sources)

    assert groups[0].verdict["status"] == "conflict_pending"
    assert groups[0].verdict["resolution"] is None


@pytest.mark.asyncio
async def test_same_tier_conflict_within_one_day_stays_pending():
    # Recency is day-granular: extraction timestamps from the same run must
    # not silently resolve a same-tier conflict (ticket 08: 进入待审).
    sources = [_source("S-001", "a.example", "A"), _source("S-002", "b.example", "A")]
    items = [
        _item("E-001", "S-001", value=100, extracted_at="2026-09-29T01:00:00+00:00"),
        _item("E-002", "S-002", value=200, extracted_at="2026-09-29T23:00:00+00:00"),
    ]

    groups = await _adjudicate(items, sources)

    assert groups[0].verdict["status"] == "conflict_pending"
    assert groups[0].verdict["resolution"] is None


@pytest.mark.asyncio
async def test_representative_prefers_best_tier_then_newest():
    sources = [
        _source("S-001", "blog.example", "C"),
        _source("S-002", "stats.gov.cn", "A"),
    ]
    items = [
        _item("E-001", "S-001", value_raw="100万辆"),
        _item("E-002", "S-002", value_raw="100万辆"),
    ]

    groups = await _adjudicate(items, sources)

    assert groups[0].representative["evidence_id"] == "E-002"
    assert groups[0].representative["source_id"] == "S-002"


# --------------------------------------------------------- layer integration


class FakeExtractionLLM:
    def __init__(self, items_by_source):
        self.items_by_source = items_by_source
        self.calls = []

    async def __call__(self, prompt):
        self.calls.append(prompt)
        if "来源分级" in prompt:
            domain = ""
            for line in prompt.splitlines():
                if line.startswith("域名："):
                    domain = line[len("域名：") :].strip()
            return json.dumps(
                {"tier": "C", "publisher": domain or "某站点", "org_type": "portal"},
                ensure_ascii=False,
            )
        if "证据抽取" in prompt:
            for url, items in self.items_by_source.items():
                if url in prompt:
                    return json.dumps(items, ensure_ascii=False)
            return "[]"
        return "[]"


def _config(**overrides):
    values = {
        "evidence_extraction_enabled": True,
        "adjudication_enabled": True,
        "evidence_llm": "fast",
        "adjudication_llm": "fast",
        "evidence_max_sources": 100,
        "evidence_max_chars_per_source": 30000,
        "evidence_chunk_size": 8000,
        "evidence_chunk_overlap": 400,
        "evidence_concurrency": 4,
        "reliability_rules_path": "",
        "tier_cache_path": "",
        "adjudication_rules_path": "",
        "fast_llm_provider": "fake",
        "fast_llm_model": "model",
        "fast_token_limit": 4000,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _raw_item(value, value_raw, quote):
    return {
        "entity": "某公司",
        "metric": "新能源汽车销量",
        "value_type": "number",
        "value": value,
        "value_raw": value_raw,
        "unit": "辆",
        "period": {"type": "year", "raw": "2024年"},
        "scope": "全年累计",
        "quote": quote,
    }


CONTENT_A = "2024 年，某公司新能源汽车销量为100万辆。" + "补充正文。" * 60
CONTENT_B = "2024 年，该企业新能源车型累计售出101万辆，同比稳步增长。" + "补充正文。" * 60

ITEM_A = _raw_item(1000000, "100万辆", "某公司新能源汽车销量为100万辆")
ITEM_B = _raw_item(1010000, "101万辆", "该企业新能源车型累计售出101万辆")


async def _build_layer_artifact(tmp_path, *, adjudication_enabled):
    sources = [
        {"url": "https://a.example/1", "title": "A", "raw_content": CONTENT_A},
        {"url": "https://b.example/2", "title": "B", "raw_content": CONTENT_B},
    ]
    llm = FakeExtractionLLM(
        {"https://a.example/1": [ITEM_A], "https://b.example/2": [ITEM_B]}
    )
    layer = EvidenceLayer(
        config=_config(adjudication_enabled=adjudication_enabled),
        llm=llm,
        cache_path="",
    )
    return await layer.build(sources, research_id="research_t07", query="新能源")


@pytest.mark.asyncio
async def test_layer_adjudication_enabled_produces_schema_v2(tmp_path):
    artifact = await _build_layer_artifact(tmp_path, adjudication_enabled=True)

    payload = artifact.to_dict()
    assert payload["schema_version"] == 2
    assert len(payload["groups"]) == 1
    group = payload["groups"][0]
    assert group["verdict"]["status"] == "cross_validated"
    assert group["effective_status"] == "cross_validated"

    summary = payload["summary"]
    assert summary["groups"] == 1
    assert summary["verdicts"]["cross_validated"] == 1
    assert summary["pending"] == 0
    assert summary["reviewed"] == 0

    markdown = artifact.to_markdown()
    assert "## 证据组与裁决" in markdown
    assert "G-001" in markdown


@pytest.mark.asyncio
async def test_layer_adjudication_disabled_keeps_schema_v1(tmp_path):
    artifact = await _build_layer_artifact(tmp_path, adjudication_enabled=False)

    payload = artifact.to_dict()
    assert payload["schema_version"] == 1
    assert "groups" not in payload
    assert payload["summary"].keys() == {
        "sources",
        "sources_scraped",
        "evidence",
        "rejected",
        "by_tier",
    }
