"""Ticket 03 regression suite: top-level trigger + sub-researcher suppression.

Runs the real GPTResearcher wiring with stubbed research execution and a
fake LLM patched into the evidence layer's default adapter, so the whole
trigger/suppression behaviour is exercised offline.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gpt_researcher import GPTResearcher
from gpt_researcher.config import Config
from gpt_researcher.utils.enum import ReportType


CONTENT = "2024 年，某公司新能源汽车销量为1288.8万辆。" + "补充正文。" * 60

SOURCES = [
    {"url": "https://stats.gov.cn/a", "title": "统计", "raw_content": CONTENT},
    {"url": "https://unknown-blog.example/b", "title": "未知", "raw_content": CONTENT},
]

EVIDENCE_ITEM = {
    "entity": "某公司",
    "metric": "新能源汽车销量",
    "value_type": "number",
    "value": "1288.8万",
    "value_raw": "1288.8万辆",
    "unit": "辆",
    "period": {"type": "year", "raw": "2024年"},
    "scope": "全年累计销量",
    "quote": "某公司新能源汽车销量为1288.8万辆",
}


class FakeWebsocket:
    def __init__(self):
        self.messages = []

    async def send_json(self, data):
        self.messages.append(data)


def _patch_layer_llm(monkeypatch, items=None):
    items = items if items is not None else [EVIDENCE_ITEM]

    async def fake_create_chat_completion(*args, **kwargs):
        prompt = kwargs["messages"][-1]["content"]
        if "来源分级" in prompt:
            return '{"tier": "C", "publisher": "某站点", "org_type": "portal"}'
        if "证据抽取" in prompt:
            return json.dumps(items, ensure_ascii=False)
        if "逐字" in prompt:
            return '{"quote": ""}'
        return "[]"

    monkeypatch.setattr(
        "gpt_researcher.evidence.layer.create_chat_completion",
        fake_create_chat_completion,
    )


def _make_researcher(monkeypatch, tmp_path, *, enabled=True, is_sub_researcher=False,
                     report_type=ReportType.ResearchReport.value, sources=None):
    monkeypatch.chdir(tmp_path)
    # The real constructor builds an embeddings client; a dummy key keeps that
    # offline while the network kill-switch guards any accidental call.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    websocket = FakeWebsocket()
    researcher = GPTResearcher(
        query="新能源行业",
        report_type=report_type,
        agent="agent",
        role="role",
        websocket=websocket,
        is_sub_researcher=is_sub_researcher,
    )
    researcher.cfg.evidence_extraction_enabled = enabled
    researcher.cfg.tier_cache_path = str(tmp_path / "tier_cache.json")
    researcher.research_sources = list(sources if sources is not None else SOURCES)
    return researcher, websocket


def _stub_research(researcher, context="ctx"):
    researcher.research_conductor.conduct_research = AsyncMock(return_value=context)


def _evidence_events(websocket):
    return [m for m in websocket.messages if m.get("type") == "evidence"]


def test_config_default_is_off():
    assert Config().evidence_extraction_enabled is False


@pytest.mark.asyncio
async def test_disabled_keeps_existing_behavior(monkeypatch, tmp_path):
    researcher, websocket = _make_researcher(monkeypatch, tmp_path, enabled=False)
    _stub_research(researcher)

    context = await researcher.conduct_research()

    assert context == "ctx"
    assert researcher.evidence_artifact is None
    assert researcher.evidence_artifact_paths == {}
    assert _evidence_events(websocket) == []
    assert not (tmp_path / "outputs").exists()


@pytest.mark.asyncio
async def test_enabled_top_level_produces_artifact_paths_and_events(monkeypatch, tmp_path):
    _patch_layer_llm(monkeypatch)
    researcher, websocket = _make_researcher(monkeypatch, tmp_path, enabled=True)
    _stub_research(researcher)

    await researcher.conduct_research()

    artifact = researcher.evidence_artifact
    assert artifact is not None
    assert artifact.summary["sources"] == 2
    assert artifact.summary["by_tier"] == {"A": 1, "B": 0, "C": 1, "D": 0}
    assert len(artifact.evidence) >= 1

    paths = researcher.evidence_artifact_paths
    assert set(paths) == {"json", "md"}
    assert all("\\" not in p for p in paths.values())
    assert (tmp_path / paths["json"]).exists()
    assert (tmp_path / paths["md"]).exists()

    events = _evidence_events(websocket)
    assert [e["content"] for e in events] == ["started", "completed"]
    assert events[1]["metadata"]["by_tier"]["A"] == 1
    assert events[1]["metadata"]["evidence"] == len(artifact.evidence)


@pytest.mark.asyncio
async def test_sub_researcher_never_triggers_even_when_enabled(monkeypatch, tmp_path):
    _patch_layer_llm(monkeypatch)
    researcher, websocket = _make_researcher(
        monkeypatch, tmp_path, enabled=True, is_sub_researcher=True
    )
    _stub_research(researcher)

    await researcher.conduct_research()

    assert researcher.evidence_artifact is None
    assert researcher.evidence_artifact_paths == {}
    assert _evidence_events(websocket) == []
    assert not (tmp_path / "outputs").exists()


@pytest.mark.asyncio
async def test_deep_path_triggers_after_sources_aggregated(monkeypatch, tmp_path):
    _patch_layer_llm(monkeypatch)
    researcher, websocket = _make_researcher(
        monkeypatch, tmp_path, enabled=True, report_type=ReportType.DeepResearch.value
    )

    deep_sources = [
        {"url": "https://stats.gov.cn/deep", "title": "deep", "raw_content": CONTENT},
        {"url": "https://unknown-blog.example/deep", "title": "deep2", "raw_content": CONTENT},
    ]

    async def fake_deep_run(on_progress=None):
        researcher.research_sources = deep_sources
        researcher.visited_urls = {"https://stats.gov.cn/deep"}
        return "deep context"

    researcher.deep_researcher = SimpleNamespace(
        breadth=1,
        depth=1,
        concurrency_limit=1,
        run=fake_deep_run,
    )

    context = await researcher.conduct_research()

    assert context == "deep context"
    artifact = researcher.evidence_artifact
    assert artifact is not None
    assert {p.url for p in artifact.sources} == {
        "https://stats.gov.cn/deep",
        "https://unknown-blog.example/deep",
    }
    assert [e["content"] for e in _evidence_events(websocket)] == ["started", "completed"]


@pytest.mark.asyncio
async def test_deep_subquery_researcher_is_created_as_sub_researcher(monkeypatch):
    import gpt_researcher as package
    from gpt_researcher.skills.deep_research import DeepResearchSkill

    created = []

    class FakeNestedResearcher:
        def __init__(self, **kwargs):
            created.append(kwargs)
            self.visited_urls = set()
            self.research_sources = []

        async def conduct_research(self):
            return "nested context"

    monkeypatch.setattr(package, "GPTResearcher", FakeNestedResearcher)

    parent = SimpleNamespace(
        cfg=SimpleNamespace(deep_research_breadth=1, deep_research_depth=1,
                            deep_research_concurrency=1),
        websocket=None,
        tone=None,
        headers={},
        visited_urls=set(),
        mcp_configs=None,
        mcp_strategy="fast",
        query="q",
    )
    skill = DeepResearchSkill(parent)
    skill.generate_search_queries = AsyncMock(
        return_value=[{"query": "sub query", "researchGoal": "goal"}]
    )
    skill.process_research_results = AsyncMock(
        return_value={"learnings": [], "followUpQuestions": [], "citations": {}}
    )

    await skill.deep_research("q", breadth=1, depth=1)

    assert created and all(
        kwargs.get("is_sub_researcher") is True for kwargs in created
    )


@pytest.mark.asyncio
async def test_detailed_subtopic_researcher_is_created_as_sub_researcher(monkeypatch):
    import backend.report_type.detailed_report.detailed_report as detailed_module

    created = []

    class FakeSubtopicResearcher:
        def __init__(self, **kwargs):
            created.append(kwargs)
            self.context = []
            self.visited_urls = set()
            self.agent = "agent"
            self.role = "role"
            self.mcp_configs = None
            self.mcp_strategy = "fast"
            self.cfg = SimpleNamespace(max_search_results_per_query=5)

        async def conduct_research(self):
            return self.context

        async def get_draft_section_titles(self, task):
            return "## 草稿"

        async def get_similar_written_contents_by_draft_section_titles(self, *args):
            return []

        async def write_report(self, **kwargs):
            return "subtopic report"

    monkeypatch.setattr(detailed_module, "GPTResearcher", FakeSubtopicResearcher)

    report = object.__new__(detailed_module.DetailedReport)
    report.query = "主查询"
    report.report_source = "web"
    report.websocket = None
    report.headers = {}
    report.subtopics = []
    report.query_domains = []
    report.global_urls = set()
    report.global_context = []
    report.global_written_sections = []
    report.existing_headers = []
    report.max_search_results = None
    report.tone = None
    report.source_urls = []
    report.complement_source_urls = False
    report.gpt_researcher = SimpleNamespace(
        agent="agent",
        role="role",
        mcp_configs=None,
        mcp_strategy="fast",
        extract_headers=lambda text: [],
        extract_sections=lambda text: [],
    )

    result = await report._get_subtopic_report({"task": "子主题"})

    assert result["report"] == "subtopic report"
    assert created and all(kwargs.get("is_sub_researcher") is True for kwargs in created)
