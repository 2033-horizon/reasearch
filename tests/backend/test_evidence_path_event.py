"""Ticket 05 regression suite: evidence path event on the WebSocket backend.

Asserts that a completed research run exposes the evidence artifact download
paths through the existing ``path`` event, formatted like report files
(forward slashes, URL-encoded), and that runs without evidence (disabled)
keep the event unchanged.
"""

import json
from types import SimpleNamespace

import pytest

from backend.server import server_utils


class FakeWebsocket:
    def __init__(self):
        self.messages = []

    async def send_json(self, data):
        self.messages.append(data)


class FakeManager:
    def __init__(self, report, researcher):
        self.report = report
        self.researcher = researcher

    async def start_streaming(self, *args, **kwargs):
        return self.report, self.researcher


@pytest.fixture
def stub_report_files(monkeypatch):
    async def fake_generate_report_files(report, filename):
        return {
            "pdf": "outputs/report.pdf",
            "docx": "outputs/report.docx",
            "md": "outputs/report.md",
        }

    monkeypatch.setattr(server_utils, "generate_report_files", fake_generate_report_files)


def _start_message(task="新能源汽车调研"):
    return "start " + json.dumps({"task": task, "report_type": "research_report"})


async def _run(monkeypatch, tmp_path, researcher, stub_report_files):
    monkeypatch.chdir(tmp_path)
    websocket = FakeWebsocket()
    manager = FakeManager("report text", researcher)
    await server_utils.handle_start_command(websocket, _start_message(), manager)
    return websocket


def _path_event(websocket):
    events = [m for m in websocket.messages if m.get("type") == "path"]
    assert len(events) == 1
    return events[0]["output"]


@pytest.mark.asyncio
async def test_path_event_includes_evidence_download_paths(
    monkeypatch, tmp_path, stub_report_files
):
    evidence_paths = {
        "json": "outputs/research_abc.evidence.json",
        "md": "outputs/research_abc.evidence.md",
    }
    researcher = SimpleNamespace(evidence_artifact_paths=evidence_paths)

    output = _path_event(await _run(monkeypatch, tmp_path, researcher, stub_report_files))

    assert output["evidence"] == evidence_paths
    assert "\\" not in json.dumps(output["evidence"])
    for path in output["evidence"].values():
        assert path.startswith("outputs/")


@pytest.mark.asyncio
async def test_path_event_without_evidence_paths_is_unchanged(
    monkeypatch, tmp_path, stub_report_files
):
    researcher = SimpleNamespace(evidence_artifact_paths={})

    output = _path_event(await _run(monkeypatch, tmp_path, researcher, stub_report_files))

    assert "evidence" not in output
    assert output["md"] == "outputs/report.md"


@pytest.mark.asyncio
async def test_path_event_tolerates_missing_researcher(
    monkeypatch, tmp_path, stub_report_files
):
    # Multi-agents runs return no researcher; the listener must not crash.
    output = _path_event(await _run(monkeypatch, tmp_path, None, stub_report_files))

    assert "evidence" not in output


@pytest.mark.asyncio
async def test_path_event_includes_review_entry_for_adjudicated_runs(
    monkeypatch, tmp_path, stub_report_files
):
    artifact = SimpleNamespace(groups=[{"id": "G-001"}], research_id="research_abc")
    researcher = SimpleNamespace(
        evidence_artifact_paths={},
        evidence_artifact=artifact,
        evidence_run_id=None,
    )

    output = _path_event(await _run(monkeypatch, tmp_path, researcher, stub_report_files))

    assert output["review"] == "/review/research_abc"
    assert output["report_version"] == 1


@pytest.mark.asyncio
async def test_path_event_reports_store_version(monkeypatch, tmp_path, stub_report_files):
    from gpt_researcher.evidence import EvidenceArtifact, EvidenceStore

    db = tmp_path / "evidence.db"
    store = EvidenceStore(str(db))
    artifact = EvidenceArtifact(
        research_id="research_abc",
        query="q",
        sources=[],
        evidence=[],
        rejected=[],
        schema_version=2,
        groups=[],
    )
    store.record_run(artifact, version=1)
    run_id = store.record_run(artifact, version=2)
    store.close()

    researcher = SimpleNamespace(
        evidence_artifact_paths={},
        evidence_artifact=SimpleNamespace(groups=[], research_id="research_abc"),
        evidence_run_id=run_id,
        cfg=SimpleNamespace(evidence_db_path=str(db)),
    )

    output = _path_event(await _run(monkeypatch, tmp_path, researcher, stub_report_files))

    assert output["report_version"] == 2

    store = EvidenceStore(str(db))
    runs = store.list_runs("research_abc")
    store.close()
    assert runs[-1]["report_paths"]["md"] == "outputs/report.md"


@pytest.mark.asyncio
async def test_path_event_without_groups_has_no_review_entry(
    monkeypatch, tmp_path, stub_report_files
):
    artifact = SimpleNamespace(groups=None, research_id="research_abc")
    researcher = SimpleNamespace(
        evidence_artifact_paths={},
        evidence_artifact=artifact,
        evidence_run_id=None,
    )

    output = _path_event(await _run(monkeypatch, tmp_path, researcher, stub_report_files))

    assert "review" not in output
    assert "report_version" not in output
