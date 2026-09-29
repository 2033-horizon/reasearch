"""Ticket 12 regression suite: review loop + regeneration.

Runs the FastAPI review router with a temporary SQLite store and fake writing
LLM: pending groups list with full source comparison, accept/reject/
set_representative actions persist reviewer + time and feed effective
conclusions, and regeneration re-runs only the writing stage producing a new
version while old versions stay recorded.
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.server import review
from gpt_researcher.evidence import (
    EvidenceArtifact,
    EvidenceGroup,
    EvidenceItem,
    EvidenceStore,
    SourceProfile,
    build_citations,
)


def _source(source_id, domain, tier, publisher):
    return SourceProfile(
        id=source_id,
        url=f"https://{domain}/x",
        domain=domain,
        tier=tier,
        assigned_by="rule",
        publisher=publisher,
        title=f"{publisher}页面",
        scraped=True,
    )


def _evidence(item_id, source_id, value_raw, scope="全年累计"):
    return EvidenceItem(
        id=item_id,
        source_id=source_id,
        metric="新能源汽车销量",
        value_type="number",
        value=100,
        value_raw=value_raw,
        quote=f"{value_raw} 的原文引用",
        extractor="fake:model",
        entity="某公司",
        unit="万辆",
        period={"type": "year", "raw": "2024年"},
        scope=scope,
        extracted_at="2026-09-29T00:00:00+00:00",
    )


def _seed(research_id="research_r12", *, pending=True, accepted=True) -> tuple[EvidenceStore, int]:
    sources = [
        _source("S-001", "a.example", "C", "甲媒体"),
        _source("S-002", "b.example", "D", "乙媒体"),
        _source("S-003", "stats.gov.cn", "A", "国家统计局"),
    ]
    evidence = [
        _evidence("E-001", "S-001", "100万辆"),
        _evidence("E-002", "S-002", "200万辆"),
        _evidence("E-003", "S-003", "100万辆"),
    ]
    groups: list[EvidenceGroup] = []
    if pending:
        groups.append(
            EvidenceGroup(
                id="G-001",
                key={"entity": "某公司", "metric": "新能源汽车销量"},
                scope="全年累计",
                members=["E-001", "E-002"],
                source_ids=["S-001", "S-002"],
                independent_sources=1,
                verdict={
                    "status": "conflict_pending",
                    "reason": "数值超出容差且等级与时效均无法裁定，进入待审",
                    "resolution": None,
                    "best_tier": "C",
                    "required": 2,
                    "independent_sources": 1,
                },
                representative={
                    "evidence_id": "E-001",
                    "source_id": "S-001",
                    "value_raw": "100万辆",
                },
            )
        )
    if accepted:
        groups.append(
            EvidenceGroup(
                id="G-002",
                key={"entity": "某公司", "metric": "行业销量"},
                scope="全年累计",
                members=["E-003"],
                source_ids=["S-003"],
                independent_sources=1,
                verdict={"status": "accepted", "reason": "A 级单条达标"},
                representative={
                    "evidence_id": "E-003",
                    "source_id": "S-003",
                    "value_raw": "100万辆",
                },
            )
        )
    artifact = EvidenceArtifact(
        research_id=research_id,
        query="新能源行业",
        sources=sources,
        evidence=evidence,
        rejected=[],
        schema_version=2,
        groups=groups,
    )
    artifact.citations = build_citations(artifact)
    store = EvidenceStore(review._db_path())
    run_id = store.record_run(artifact)
    return store, run_id


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("EVIDENCE_DB_PATH", str(tmp_path / "evidence.db"))
    monkeypatch.chdir(tmp_path)
    app = FastAPI()
    app.include_router(review.router)
    return TestClient(app)


def test_review_page_is_served(client):
    response = client.get("/review/research_r12")

    assert response.status_code == 200
    assert "证据复核" in response.text
    assert "research_r12" in response.text


def test_pending_groups_show_full_source_comparison(client):
    store, _ = _seed()
    store.close()

    response = client.get("/api/research/research_r12/evidence")

    assert response.status_code == 200
    payload = response.json()
    pending = payload["pending_groups"]
    assert len(pending) == 1
    assert pending[0]["id"] == "G-001"
    members = pending[0]["member_details"]
    assert {m["source"]["tier"] for m in members} == {"C", "D"}
    assert {m["evidence"]["value_raw"] for m in members} == {"100万辆", "200万辆"}
    assert all(m["evidence"]["quote"] for m in members)
    assert all(m["source"]["url"].startswith("https://") for m in members)


def test_accept_records_reviewer_and_time_and_enters_citations(client):
    store, _ = _seed()
    store.close()

    response = client.post(
        "/api/research/research_r12/reviews",
        json={"group_id": "G-001", "action": "accept", "reviewer": "张三"},
    )

    assert response.status_code == 200
    review_record = response.json()["review"]
    assert review_record["reviewer"] == "张三"
    assert review_record["created_at"]

    store = EvidenceStore(review._db_path())
    artifact = store.load_artifact(store.latest_run("research_r12")["run_id"])
    store.close()
    group = next(g for g in artifact.groups if g.id == "G-001")
    assert group.review["action"] == "accept"
    assert group.effective_status == "accepted"
    assert any(
        entry["group_id"] == "G-001" for entry in (artifact.citations or {}).values()
    )

    overview = client.get("/api/research/research_r12/evidence").json()
    assert overview["pending_groups"] == []


def test_reject_excludes_group_from_effective_conclusions(client):
    store, _ = _seed()
    store.close()

    response = client.post(
        "/api/research/research_r12/reviews",
        json={"group_id": "G-002", "action": "reject", "reviewer": "李四"},
    )
    assert response.status_code == 200
    assert response.json()["group"]["effective_status"] == "rejected"

    store = EvidenceStore(review._db_path())
    artifact = store.load_artifact(store.latest_run("research_r12")["run_id"])
    store.close()
    assert all(
        entry["group_id"] != "G-002" for entry in (artifact.citations or {}).values()
    )


def test_set_representative_overrides_citations(client):
    store, _ = _seed()
    store.close()

    missing = client.post(
        "/api/research/research_r12/reviews",
        json={"group_id": "G-001", "action": "set_representative"},
    )
    assert missing.status_code == 400

    response = client.post(
        "/api/research/research_r12/reviews",
        json={
            "group_id": "G-001",
            "action": "set_representative",
            "representative_source_id": "S-002",
            "reviewer": "王五",
        },
    )
    assert response.status_code == 200
    assert response.json()["group"]["effective_status"] == "accepted"

    store = EvidenceStore(review._db_path())
    artifact = store.load_artifact(store.latest_run("research_r12")["run_id"])
    store.close()
    citation = next(
        entry
        for entry in (artifact.citations or {}).values()
        if entry["group_id"] == "G-001"
    )
    assert citation["source_id"] == "S-002"
    assert citation["url"] == "https://b.example/x"


def test_regenerate_writes_new_version_and_keeps_history(client, monkeypatch):
    store, _ = _seed()
    store.close()
    client.post(
        "/api/research/research_r12/reviews",
        json={"group_id": "G-001", "action": "accept", "reviewer": "张三"},
    )

    generate = AsyncMock(return_value="正文数据为100万辆[^1]，其它为补充说明。")
    monkeypatch.setattr(review, "generate_report", generate)

    response = client.post("/api/research/research_r12/regenerate")

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == 2
    assert payload["paths"]["md"].endswith(".v2.md")
    assert "[^1]" in payload["report"]
    assert "[^1]:" in payload["report"]

    # Only the writing stage ran, with the evidence context as its input.
    assert generate.await_count == 1
    context = generate.await_args.kwargs["context"]
    assert "[^1]" in context and "原文引用" in context

    overview = client.get("/api/research/research_r12/evidence").json()
    versions = {entry["version"]: entry for entry in overview["versions"]}
    assert set(versions) == {1, 2}
    assert versions[2]["report_paths"]["md"].endswith(".v2.md")
    # Version 1 is retained with its original (empty) report paths.


def test_regenerate_blocked_without_valid_conclusions(client, monkeypatch):
    store, _ = _seed(accepted=False)
    store.close()
    generate = AsyncMock(return_value="不应被调用")
    monkeypatch.setattr(review, "generate_report", generate)

    response = client.post("/api/research/research_r12/regenerate")

    assert response.status_code == 409
    assert "证据不足" in response.json()["detail"]
    assert generate.await_count == 0


def test_reviews_are_local_to_one_research(client):
    store, _ = _seed()
    store.record_run(
        EvidenceArtifact(
            research_id="research_other",
            query="其它调研",
            sources=[],
            evidence=[],
            rejected=[],
            schema_version=2,
            groups=[],
        )
    )
    store.close()

    client.post(
        "/api/research/research_r12/reviews",
        json={"group_id": "G-001", "action": "accept", "reviewer": "张三"},
    )

    other = client.get("/api/research/research_other/evidence").json()
    assert other["groups"] == []
    store = EvidenceStore(review._db_path())
    assert store.reviews_for_run("research_other") == []
    store.close()
