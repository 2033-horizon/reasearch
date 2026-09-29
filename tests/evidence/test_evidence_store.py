"""Ticket 09 regression suite: SQLite evidence store.

Runs/sources/evidence/groups round-trip through the store (artifacts are
exported from it), schema version + append-only migration hold, repeated runs
append rather than overwrite, and the extraction/judgement caches survive
across research runs. All tests use temporary databases and fake LLMs.
"""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from gpt_researcher.evidence import (
    DB_SCHEMA_VERSION,
    EvidenceArtifact,
    EvidenceGroup,
    EvidenceItem,
    EvidenceLayer,
    EvidenceStore,
    RejectedItem,
    SourceProfile,
    build_citations,
)
def _source(source_id, domain, tier, publisher=None):
    return SourceProfile(
        id=source_id,
        url=f"https://{domain}/a",
        domain=domain,
        tier=tier,
        assigned_by="rule",
        publisher=publisher,
        scraped=True,
        title=f"标题 {source_id}",
    )


def _evidence(item_id, source_id, *, value=100, scope="全年累计"):
    return EvidenceItem(
        id=item_id,
        source_id=source_id,
        metric="新能源汽车销量",
        value_type="number",
        value=value,
        value_raw=str(value),
        quote=f"引用 {item_id}",
        extractor="fake:model",
        entity="新能源汽车",
        unit="辆",
        period={"type": "year", "raw": "2024年"},
        scope=scope,
        extracted_at="2026-09-29T00:00:00+00:00",
    )


def _artifact(*, research_id="research_store1", with_groups=False) -> EvidenceArtifact:
    sources = [_source("S-001", "a.example", "C"), _source("S-002", "b.example", "D")]
    evidence = [_evidence("E-001", "S-001"), _evidence("E-002", "S-002", value=101)]
    groups = None
    if with_groups:
        groups = [
            EvidenceGroup(
                id="G-001",
                key={"entity": "新能源汽车", "metric": "新能源汽车销量"},
                scope="全年累计",
                members=["E-001", "E-002"],
                source_ids=["S-001", "S-002"],
                independent_sources=1,
                verdict={
                    "status": "insufficient_pending",
                    "rule": "tier_minimum:C=2",
                    "reason": "独立来源不足",
                    "required": 2,
                    "independent_sources": 1,
                    "best_tier": "C",
                },
                representative={"evidence_id": "E-001", "source_id": "S-001", "value": 100},
                merge_log=[{"type": "metric_merged", "metrics": ["销量", "销售量"]}],
            )
        ]
    artifact = EvidenceArtifact(
        research_id=research_id,
        query="新能源行业",
        sources=sources,
        evidence=evidence,
        rejected=[RejectedItem(source_id="S-001", reason="quote_not_found", detail="示例")],
        schema_version=2 if with_groups else 1,
        groups=groups,
        generated_at="2026-09-29T01:02:03+00:00",
    )
    if with_groups:
        artifact.citations = build_citations(artifact)
    return artifact


def test_record_and_load_round_trip(tmp_path):
    store = EvidenceStore(str(tmp_path / "evidence.db"))
    artifact = _artifact(with_groups=True)

    run_id = store.record_run(artifact)
    loaded = store.load_artifact(run_id)

    assert loaded is not None
    assert loaded.to_dict() == artifact.to_dict()
    store.close()


def test_export_artifact_matches_store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = EvidenceStore(str(tmp_path / "evidence.db"))
    artifact = _artifact(with_groups=True)
    run_id = store.record_run(artifact)

    paths = store.export_artifact(run_id)

    payload = json.loads((tmp_path / paths["json"]).read_text(encoding="utf-8"))
    assert payload == artifact.to_dict()
    assert (tmp_path / paths["md"]).exists()
    assert paths["json"].startswith("outputs/")
    store.close()


def test_schema_version_and_reopen_keeps_history(tmp_path):
    path = str(tmp_path / "evidence.db")
    store = EvidenceStore(path)
    assert store.schema_version == DB_SCHEMA_VERSION
    run_id = store.record_run(_artifact())
    store.close()

    reopened = EvidenceStore(path)
    assert reopened.schema_version == DB_SCHEMA_VERSION
    assert reopened.load_artifact(run_id) is not None
    reopened.close()


def test_empty_legacy_database_is_migrated(tmp_path):
    raw = sqlite3.connect(str(tmp_path / "legacy.db"))
    raw.execute("PRAGMA user_version = 0")
    raw.commit()
    raw.close()

    store = EvidenceStore(str(tmp_path / "legacy.db"))
    assert store.schema_version == DB_SCHEMA_VERSION
    store.record_run(_artifact())
    store.close()


def test_repeated_runs_append_new_versions(tmp_path):
    store = EvidenceStore(str(tmp_path / "evidence.db"))
    artifact = _artifact()

    first = store.record_run(artifact)
    second = store.record_run(artifact)

    runs = store.list_runs("research_store1")
    assert [run["version"] for run in runs] == [1, 2]
    assert [run["run_id"] for run in runs] == [first, second]
    assert store.next_version("research_store1") == 3
    assert store.load_artifact(first) is not None
    assert store.load_artifact(second) is not None
    store.close()


def test_pending_group_details_include_source_comparison(tmp_path):
    store = EvidenceStore(str(tmp_path / "evidence.db"))
    store.record_run(_artifact(with_groups=True))

    details = store.pending_group_details("research_store1")

    assert len(details) == 1
    assert details[0]["effective_status"] == "insufficient_pending"
    members = details[0]["member_details"]
    assert len(members) == 2
    assert members[0]["evidence"]["id"] == "E-001"
    assert members[0]["source"]["tier"] == "C"
    store.close()


# ------------------------------------------------------------ layer + caches


class CountingExtractionLLM:
    def __init__(self, items):
        self.items = items
        self.extract_calls = 0

    async def __call__(self, prompt):
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
            self.extract_calls += 1
            return json.dumps(self.items, ensure_ascii=False)
        return "[]"


def _config(**overrides):
    values = {
        "evidence_extraction_enabled": True,
        "adjudication_enabled": False,
        "evidence_llm": "fast",
        "evidence_max_sources": 100,
        "evidence_max_chars_per_source": 30000,
        "evidence_chunk_size": 8000,
        "evidence_chunk_overlap": 400,
        "evidence_concurrency": 4,
        "reliability_rules_path": "",
        "tier_cache_path": "",
        "adjudication_rules_path": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _content(quote):
    return quote + "。" + "这是用于撑满最小正文长度的补充材料。" * 20


ITEM = {
    "entity": "某公司",
    "metric": "新能源汽车销量",
    "value_type": "number",
    "value": 1000000,
    "value_raw": "100万辆",
    "unit": "辆",
    "period": {"type": "year", "raw": "2024年"},
    "scope": "全年累计",
    "quote": "某公司新能源汽车销量为100万辆",
}


@pytest.mark.asyncio
async def test_extraction_cache_reuses_verified_items(tmp_path):
    db = str(tmp_path / "evidence.db")
    sources = [
        {
            "url": "https://a.example/1",
            "title": "A",
            "raw_content": _content(ITEM["quote"]),
        }
    ]

    first_llm = CountingExtractionLLM([ITEM])
    first_layer = EvidenceLayer(_config(), llm=first_llm, store_path=db, cache_path="")
    first = await first_layer.build(sources, research_id="r1", query="q")
    first_layer.close()
    assert first_llm.extract_calls == 1
    assert len(first.evidence) == 1

    second_llm = CountingExtractionLLM([])
    second_layer = EvidenceLayer(_config(), llm=second_llm, store_path=db, cache_path="")
    second = await second_layer.build(sources, research_id="r2", query="q")
    second_layer.close()

    assert second_llm.extract_calls == 0
    assert [item.quote for item in second.evidence] == [
        item.quote for item in first.evidence
    ]
    assert second.evidence[0].extracted_at == first.evidence[0].extracted_at


@pytest.mark.asyncio
async def test_extraction_cache_misses_when_content_changes(tmp_path):
    db = str(tmp_path / "evidence.db")
    original = [{"url": "https://a.example/1", "title": "A", "raw_content": _content(ITEM["quote"])}]
    changed = [
        {
            "url": "https://a.example/1",
            "title": "A",
            "raw_content": _content("该页面内容已经更新，原文引用不再存在") ,
        }
    ]

    first_layer = EvidenceLayer(
        _config(), llm=CountingExtractionLLM([ITEM]), store_path=db, cache_path=""
    )
    await first_layer.build(original, research_id="r1", query="q")
    first_layer.close()

    fresh_llm = CountingExtractionLLM([])
    second_layer = EvidenceLayer(
        _config(), llm=fresh_llm, store_path=db, cache_path=""
    )
    await second_layer.build(changed, research_id="r2", query="q")
    second_layer.close()

    assert fresh_llm.extract_calls == 1


class FakeAdjudicationLLM(CountingExtractionLLM):
    def __init__(self, items, metric_same=True):
        super().__init__(items)
        self.metric_same = metric_same
        self.metric_calls = 0

    async def __call__(self, prompt):
        if "指标归并" in prompt:
            self.metric_calls += 1
            return json.dumps({"same": self.metric_same, "reason": "测试"})
        return await super().__call__(prompt)


@pytest.mark.asyncio
async def test_judgement_cache_persists_in_store(tmp_path):
    db = str(tmp_path / "evidence.db")
    sources = [
        {"url": "https://a.example/1", "title": "A", "raw_content": _content("某公司销量为100万辆")},
        {"url": "https://b.example/2", "title": "B", "raw_content": _content("该企业销售量达到101万辆")},
    ]
    item_a = {**ITEM, "metric": "销量", "quote": "某公司销量为100万辆"}
    item_b = {
        **ITEM,
        "metric": "销售量",
        "value": 1010000,
        "value_raw": "101万辆",
        "quote": "该企业销售量达到101万辆",
    }

    class RouterLLM(FakeAdjudicationLLM):
        async def __call__(self, prompt):
            if "证据抽取" in prompt:
                self.extract_calls += 1
                if "https://a.example/1" in prompt:
                    return json.dumps([item_a], ensure_ascii=False)
                return json.dumps([item_b], ensure_ascii=False)
            return await super().__call__(prompt)

    first_llm = RouterLLM([])
    first_layer = EvidenceLayer(
        _config(adjudication_enabled=True),
        llm=first_llm,
        store_path=db,
        cache_path="",
    )
    first = await first_layer.build(sources, research_id="r1", query="q")
    first_layer.close()
    assert first_llm.metric_calls == 1

    second_llm = RouterLLM([])
    second_layer = EvidenceLayer(
        _config(adjudication_enabled=True),
        llm=second_llm,
        store_path=db,
        cache_path="",
    )
    second = await second_layer.build(sources, research_id="r2", query="q")
    second_layer.close()

    assert second_llm.metric_calls == 0
    merges = [
        entry
        for group in second.groups
        for entry in group.merge_log
        if entry["type"] == "metric_merged"
    ]
    assert merges and merges[0]["by"] == "cache"
