"""Ticket 01 regression suite: source tiering slice.

Exercises the evidence module's public entry (``EvidenceLayer.build``) with
fake LLMs injected and the packaged/custom rule tables, asserting the
artifact contract (profiles, summary, JSON/Markdown files) rather than
implementation details.
"""

import json
from types import SimpleNamespace

import pytest

from gpt_researcher.evidence import EvidenceLayer, TierRules, normalize_domain


RULES_YAML = """version: 1
rules:
  - match: domain_exact
    value: stats.gov.cn
    tier: A
    publisher: 国家统计局
    org_type: government
  - match: suffix
    value: .gov.cn
    tier: A
    org_type: government
  - match: suffix
    value: .stats.gov.cn
    tier: B
    publisher: 统计子站
    org_type: institute
  - match: domain_exact
    value: people.com.cn
    tier: B
    publisher: 人民网
    org_type: central_media
"""


class FakeLLM:
    """Async fake that answers classification and extraction prompts."""

    def __init__(self, tier_response='{"tier": "B", "publisher": "某站点", "org_type": "portal"}'):
        self.tier_response = tier_response
        self.calls = []

    async def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        if "来源分级" in prompt:
            return self.tier_response
        return "[]"


def _config(**overrides):
    values = {
        "evidence_extraction_enabled": True,
        "evidence_llm": "fast",
        "evidence_max_sources": 100,
        "evidence_max_chars_per_source": 30000,
        "evidence_chunk_size": 8000,
        "evidence_chunk_overlap": 400,
        "evidence_concurrency": 4,
        "reliability_rules_path": "",
        "tier_cache_path": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _write_rules(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text(RULES_YAML, encoding="utf-8")
    return str(path)


def _sources():
    return [
        {
            "url": "https://stats.gov.cn/sj/zxfb/",
            "title": "统计公报",
            "raw_content": "2024 年全国新能源汽车产量为 1288.8 万辆。",
        },
        {
            "url": "http://people.com.cn/a?x=1",
            "title": "人民网报道",
            "raw_content": "正文内容。",
        },
        {
            "url": "https://unknown-blog.example/post/1",
            "title": "未知站点",
            "raw_content": "未知站点正文。",
        },
    ]


async def _build(tmp_path, llm, rules_path=None, cache_path=None):
    layer = EvidenceLayer(
        config=_config(),
        llm=llm,
        rules_path=rules_path or _write_rules(tmp_path),
        cache_path=cache_path,
    )
    return await layer.build(_sources(), research_id="research_abc123", query="新能源行业")


def test_normalize_domain_strips_scheme_path_port():
    assert normalize_domain("https://www.stats.gov.cn:443/sj/zxfb/?a=1#x") == "www.stats.gov.cn"
    assert normalize_domain("stats.gov.cn:8080/path") == "stats.gov.cn"
    assert normalize_domain("  Example.COM.  ") == "example.com"
    assert normalize_domain("") == ""
    assert normalize_domain(None) == ""


@pytest.mark.asyncio
async def test_profiles_match_rules_with_priority(tmp_path):
    llm = FakeLLM()
    artifact = await _build(tmp_path, llm)

    by_domain = {p.domain: p for p in artifact.sources}
    assert set(by_domain) == {"stats.gov.cn", "people.com.cn", "unknown-blog.example"}

    # Exact rule beats the broader .gov.cn suffix rule.
    stats = by_domain["stats.gov.cn"]
    assert stats.tier == "A"
    assert stats.assigned_by == "rule"
    assert stats.publisher == "国家统计局"
    assert stats.matched_rule == "domain_exact:stats.gov.cn"
    assert stats.scraped is True

    people = by_domain["people.com.cn"]
    assert people.tier == "B"
    assert people.assigned_by == "rule"
    assert people.matched_rule == "domain_exact:people.com.cn"

    # Rule-matched domains never reach the classification LLM.
    tier_calls = [c for c in llm.calls if "来源分级" in c]
    assert len(tier_calls) == 1
    assert "unknown-blog.example" in tier_calls[0]


@pytest.mark.asyncio
async def test_subdomain_hits_suffix_and_longest_suffix_wins(tmp_path):
    layer = EvidenceLayer(
        config=_config(),
        llm=FakeLLM(),
        rules_path=_write_rules(tmp_path),
    )
    sources = [
        {"url": "https://data.stats.gov.cn/x", "title": "", "raw_content": "内容"},
        {"url": "https://www.moa.gov.cn/x", "title": "", "raw_content": "内容"},
    ]
    artifact = await layer.build(sources, research_id="r1", query="q")

    tiers = {(p.domain, p.tier, p.matched_rule) for p in artifact.sources}
    assert ("data.stats.gov.cn", "B", "suffix:.stats.gov.cn") in tiers
    assert ("www.moa.gov.cn", "A", "suffix:.gov.cn") in tiers


@pytest.mark.asyncio
async def test_unknown_domain_llm_capped_at_c(tmp_path):
    llm = FakeLLM(tier_response='{"tier": "A", "publisher": "某门户", "org_type": "portal"}')
    artifact = await _build(tmp_path, llm)

    unknown = next(p for p in artifact.sources if p.domain == "unknown-blog.example")
    assert unknown.tier == "C"
    assert unknown.assigned_by == "llm"
    assert unknown.publisher == "某门户"
    assert unknown.matched_rule is None


@pytest.mark.asyncio
async def test_llm_unavailable_defaults_to_d(tmp_path):
    class BrokenLLM:
        async def __call__(self, prompt):
            raise RuntimeError("no network")

    artifact = await _build(tmp_path, BrokenLLM())
    unknown = next(p for p in artifact.sources if p.domain == "unknown-blog.example")
    assert unknown.tier == "D"
    assert unknown.assigned_by == "default"


@pytest.mark.asyncio
async def test_llm_result_is_cached_and_reused(tmp_path):
    cache_path = str(tmp_path / "tier_cache.json")
    llm = FakeLLM(tier_response='{"tier": "C", "publisher": "某站点", "org_type": "portal"}')
    sources = [
        {"url": "https://unknown-blog.example/a", "title": "", "raw_content": "正文"},
        {"url": "https://unknown-blog.example/b", "title": "", "raw_content": "正文"},
    ]
    layer = EvidenceLayer(
        config=_config(),
        llm=llm,
        rules_path=_write_rules(tmp_path),
        cache_path=cache_path,
    )
    artifact = await layer.build(sources, research_id="r1", query="q")

    tier_calls = [c for c in llm.calls if "来源分级" in c]
    assert len(tier_calls) == 1
    assert all(p.tier == "C" and p.assigned_by == "llm" for p in artifact.sources)

    # A fresh layer reading the same cache must not call the LLM again.
    fresh_llm = FakeLLM()
    fresh_layer = EvidenceLayer(
        config=_config(),
        llm=fresh_llm,
        rules_path=_write_rules(tmp_path),
        cache_path=cache_path,
    )
    fresh_artifact = await fresh_layer.build(sources[:1], research_id="r2", query="q")
    assert fresh_llm.calls == []
    assert fresh_artifact.sources[0].tier == "C"
    assert fresh_artifact.sources[0].assigned_by == "llm"


@pytest.mark.asyncio
async def test_summary_json_and_markdown_artifact(tmp_path):
    artifact = await _build(tmp_path, FakeLLM())

    payload = artifact.to_dict()
    assert payload["schema_version"] == 1
    assert payload["research_id"] == "research_abc123"
    assert payload["query"] == "新能源行业"
    assert payload["summary"] == {
        "sources": 3,
        "sources_scraped": 3,
        "evidence": 0,
        "rejected": 0,
        "by_tier": {"A": 1, "B": 1, "C": 1, "D": 0},
    }
    assert len(payload["sources"]) == 3
    assert payload["sources"][0]["id"] == "S-001"
    assert payload["evidence"] == []
    assert payload["rejected"] == []

    markdown = artifact.to_markdown()
    assert "## 证据表" in markdown
    assert "S-001" in markdown
    assert "来源等级" in markdown


@pytest.mark.asyncio
async def test_artifact_save_writes_json_and_markdown(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    artifact = await _build(tmp_path, FakeLLM())
    paths = artifact.save()

    assert paths == {
        "json": "outputs/research_abc123.evidence.json",
        "md": "outputs/research_abc123.evidence.md",
    }
    json_path = tmp_path / paths["json"]
    md_path = tmp_path / paths["md"]
    assert json_path.exists() and md_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))["schema_version"] == 1
    assert md_path.read_text(encoding="utf-8").startswith("# ")


def test_packaged_default_rules_load_without_path():
    rules = TierRules.load()
    assert rules.rules, "packaged default rule table must not be empty"
    assert all(rule.tier in {"A", "B", "C", "D"} for rule in rules.rules)


def test_custom_rules_path_overrides_default(tmp_path):
    rules = TierRules.load(_write_rules(tmp_path))
    matched, label = rules.match("www.moa.gov.cn")
    assert matched is not None and matched.tier == "A"
    assert label == "suffix:.gov.cn"

    default_rules = TierRules.load()
    default_matched, _ = default_rules.match("some-unknown-domain.example")
    assert default_matched is None


def test_packaging_declares_rules_file():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    setup_py = (root / "setup.py").read_text(encoding="utf-8")
    assert "gpt_researcher/evidence/rules" in pyproject
    assert "rules/*.yaml" in setup_py
