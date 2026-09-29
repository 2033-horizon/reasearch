"""SQLite evidence store: the system of record for phase 2 (ADR-0005).

Runs, source profiles, evidence items, groups/verdicts and reviews all live in
one SQLite file; JSON + Markdown artifacts are snapshots exported from the
store, and the extraction cache lets a repeated URL + quote reuse previous
verified items instead of re-spending extraction LLM calls. Schema evolution
uses ``PRAGMA user_version`` with an append-only migration list.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from .models import (
    SCHEMA_VERSION,
    SCHEMA_VERSION_ADJUDICATED,
    EvidenceArtifact,
    EvidenceGroup,
    EvidenceItem,
    RejectedItem,
    SourceProfile,
)
from .writing import build_citations

logger = logging.getLogger(__name__)

DB_SCHEMA_VERSION = 1

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    research_id TEXT NOT NULL,
    query TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    generated_at TEXT NOT NULL DEFAULT '',
    schema_version INTEGER NOT NULL DEFAULT 1,
    citations_json TEXT,
    summary_json TEXT NOT NULL DEFAULT '{}',
    report_paths_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_runs_research ON runs(research_id);

CREATE TABLE IF NOT EXISTS sources (
    run_id INTEGER NOT NULL,
    source_id TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    domain TEXT NOT NULL DEFAULT '',
    title TEXT,
    publisher TEXT,
    tier TEXT NOT NULL DEFAULT 'D',
    org_type TEXT,
    assigned_by TEXT NOT NULL DEFAULT 'default',
    matched_rule TEXT,
    scraped INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, source_id)
);

CREATE TABLE IF NOT EXISTS evidence (
    run_id INTEGER NOT NULL,
    evidence_id TEXT NOT NULL,
    source_id TEXT NOT NULL DEFAULT '',
    entity TEXT,
    metric TEXT NOT NULL DEFAULT '',
    value_type TEXT NOT NULL DEFAULT 'text',
    value_json TEXT,
    value_raw TEXT,
    unit TEXT,
    period_json TEXT,
    region TEXT,
    scope TEXT,
    quote TEXT,
    extracted_at TEXT,
    extractor TEXT,
    PRIMARY KEY (run_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS rejected (
    run_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    source_id TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    detail TEXT,
    PRIMARY KEY (run_id, ordinal)
);

CREATE TABLE IF NOT EXISTS groups (
    run_id INTEGER NOT NULL,
    group_id TEXT NOT NULL,
    key_json TEXT NOT NULL DEFAULT '{}',
    scope TEXT,
    members_json TEXT NOT NULL DEFAULT '[]',
    sources_json TEXT NOT NULL DEFAULT '[]',
    independent_sources INTEGER NOT NULL DEFAULT 0,
    verdict_json TEXT NOT NULL DEFAULT '{}',
    representative_json TEXT,
    merge_log_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (run_id, group_id)
);

CREATE TABLE IF NOT EXISTS reviews (
    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
    research_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    action TEXT NOT NULL,
    representative_source_id TEXT,
    reviewer TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reviews_research ON reviews(research_id, group_id);

CREATE TABLE IF NOT EXISTS judgements (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);

CREATE TABLE IF NOT EXISTS extraction_cache (
    url TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);
"""

# Append new schema revisions here; each entry migrates user_version N -> N+1.
_MIGRATIONS: list[str] = [_SCHEMA_V1]

REVIEW_ACTIONS = ("accept", "reject", "set_representative")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class EvidenceStore:
    """Single-file evidence store (runs, evidence, groups, reviews, caches)."""

    def __init__(self, path: str):
        if not path:
            raise ValueError("EvidenceStore requires a database path")
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.migrate()

    # ------------------------------------------------------------ migrations

    @property
    def schema_version(self) -> int:
        return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self) -> int:
        """Apply pending migrations; history is append-only, never rewritten."""
        with self._lock:
            version = self.schema_version
            for index in range(version, len(_MIGRATIONS)):
                with self._conn:
                    self._conn.executescript(_MIGRATIONS[index])
                    self._conn.execute(f"PRAGMA user_version = {index + 1}")
            return self.schema_version

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------ run writes

    def record_run(
        self,
        artifact: EvidenceArtifact,
        *,
        version: int | None = None,
        report_paths: dict[str, str] | None = None,
    ) -> int:
        """Persist one research run (sources, evidence, groups) and return its id."""
        research_id = artifact.research_id
        resolved_version = version if version is not None else self.next_version(research_id)
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT INTO runs (research_id, query, version, created_at,"
                " generated_at, schema_version, citations_json, summary_json,"
                " report_paths_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    research_id,
                    artifact.query,
                    int(resolved_version),
                    _now_iso(),
                    artifact.generated_at,
                    int(artifact.schema_version),
                    _dumps(artifact.citations) if artifact.citations is not None else None,
                    _dumps(artifact.summary),
                    _dumps(report_paths or {}),
                ),
            )
            run_id = int(cursor.lastrowid)

            self._conn.executemany(
                "INSERT INTO sources (run_id, source_id, url, domain, title,"
                " publisher, tier, org_type, assigned_by, matched_rule, scraped)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        run_id,
                        profile.id,
                        profile.url,
                        profile.domain,
                        profile.title,
                        profile.publisher,
                        profile.tier,
                        profile.org_type,
                        profile.assigned_by,
                        profile.matched_rule,
                        1 if profile.scraped else 0,
                    )
                    for profile in artifact.sources
                ],
            )
            self._conn.executemany(
                "INSERT INTO evidence (run_id, evidence_id, source_id, entity,"
                " metric, value_type, value_json, value_raw, unit, period_json,"
                " region, scope, quote, extracted_at, extractor)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        run_id,
                        item.id,
                        item.source_id,
                        item.entity,
                        item.metric,
                        item.value_type,
                        _dumps(item.value),
                        item.value_raw,
                        item.unit,
                        _dumps(item.period),
                        item.region,
                        item.scope,
                        item.quote,
                        item.extracted_at,
                        item.extractor,
                    )
                    for item in artifact.evidence
                ],
            )
            self._conn.executemany(
                "INSERT INTO rejected (run_id, ordinal, source_id, reason, detail)"
                " VALUES (?, ?, ?, ?, ?)",
                [
                    (run_id, ordinal, item.source_id, item.reason, item.detail)
                    for ordinal, item in enumerate(artifact.rejected)
                ],
            )
            for group in artifact.groups or []:
                self._conn.execute(
                    "INSERT INTO groups (run_id, group_id, key_json, scope,"
                    " members_json, sources_json, independent_sources,"
                    " verdict_json, representative_json, merge_log_json)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        group.id,
                        _dumps(group.key),
                        group.scope,
                        _dumps(group.members),
                        _dumps(group.source_ids),
                        int(group.independent_sources),
                        _dumps(group.verdict),
                        _dumps(group.representative),
                        _dumps(group.merge_log),
                    ),
                )
        return run_id

    def update_report_paths(self, run_id: int, report_paths: dict[str, str]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE runs SET report_paths_json = ? WHERE run_id = ?",
                (_dumps(report_paths), run_id),
            )

    def next_version(self, research_id: str) -> int:
        row = self._conn.execute(
            "SELECT MAX(version) FROM runs WHERE research_id = ?", (research_id,)
        ).fetchone()
        return int(row[0] or 0) + 1

    # ------------------------------------------------------------- run reads

    def latest_run(self, research_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE research_id = ? ORDER BY version DESC, run_id DESC LIMIT 1",
            (research_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_runs(self, research_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT run_id, research_id, query, version, created_at, report_paths_json"
            " FROM runs WHERE research_id = ? ORDER BY version ASC, run_id ASC",
            (research_id,),
        ).fetchall()
        return [
            {
                "run_id": row["run_id"],
                "research_id": row["research_id"],
                "query": row["query"],
                "version": row["version"],
                "created_at": row["created_at"],
                "report_paths": _loads(row["report_paths_json"], {}),
            }
            for row in rows
        ]

    def load_artifact(self, run_id: int) -> EvidenceArtifact | None:
        run = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if run is None:
            return None
        research_id = run["research_id"]
        sources = [
            SourceProfile(
                id=row["source_id"],
                url=row["url"],
                domain=row["domain"],
                tier=row["tier"],
                assigned_by=row["assigned_by"],
                title=row["title"],
                publisher=row["publisher"],
                org_type=row["org_type"],
                matched_rule=row["matched_rule"],
                scraped=bool(row["scraped"]),
            )
            for row in self._conn.execute(
                "SELECT * FROM sources WHERE run_id = ? ORDER BY source_id", (run_id,)
            ).fetchall()
        ]
        evidence = [
            EvidenceItem(
                id=row["evidence_id"],
                source_id=row["source_id"],
                metric=row["metric"],
                value_type=row["value_type"],
                value=_loads(row["value_json"], None),
                value_raw=row["value_raw"] or "",
                quote=row["quote"] or "",
                extractor=row["extractor"] or "unknown",
                extracted_at=row["extracted_at"] or "",
                entity=row["entity"],
                unit=row["unit"],
                period=_loads(row["period_json"], None),
                region=row["region"],
                scope=row["scope"],
            )
            for row in self._conn.execute(
                "SELECT * FROM evidence WHERE run_id = ? ORDER BY evidence_id", (run_id,)
            ).fetchall()
        ]
        rejected = [
            RejectedItem(
                source_id=row["source_id"], reason=row["reason"], detail=row["detail"] or ""
            )
            for row in self._conn.execute(
                "SELECT * FROM rejected WHERE run_id = ? ORDER BY ordinal", (run_id,)
            ).fetchall()
        ]
        group_rows = self._conn.execute(
            "SELECT * FROM groups WHERE run_id = ? ORDER BY group_id", (run_id,)
        ).fetchall()
        reviews = self._latest_reviews(research_id)
        schema_version = int(run["schema_version"] or SCHEMA_VERSION)
        groups: list[EvidenceGroup] | None = None
        if group_rows or schema_version >= SCHEMA_VERSION_ADJUDICATED:
            groups = [
                EvidenceGroup(
                    id=row["group_id"],
                    key=_loads(row["key_json"], {}),
                    scope=row["scope"],
                    members=_loads(row["members_json"], []),
                    source_ids=_loads(row["sources_json"], []),
                    independent_sources=int(row["independent_sources"]),
                    verdict=_loads(row["verdict_json"], {}),
                    representative=_loads(row["representative_json"], None),
                    merge_log=_loads(row["merge_log_json"], []),
                    review=reviews.get(row["group_id"]),
                )
                for row in group_rows
            ]
        stored_citations = _loads(run["citations_json"], None)
        artifact = EvidenceArtifact(
            research_id=research_id,
            query=run["query"] or "",
            sources=sources,
            evidence=evidence,
            rejected=rejected,
            generated_at=run["generated_at"] or run["created_at"],
            schema_version=schema_version,
            groups=groups,
            citations=stored_citations,
        )
        if stored_citations is not None or groups is not None:
            # Rebuild from the latest reviews so citation numbering reflects
            # the effective conclusions, not the frozen verdicts alone.
            artifact.citations = build_citations(artifact)
        return artifact

    def export_artifact(self, run_id: int, output_dir: str = "outputs") -> dict[str, str]:
        """Write the JSON + Markdown snapshot from the store; returns paths."""
        artifact = self.load_artifact(run_id)
        if artifact is None:
            raise KeyError(f"run {run_id} not found")
        return artifact.save(output_dir)

    # ----------------------------------------------------------- group reads

    def groups_for_run(self, run_id: int) -> list[dict[str, Any]]:
        artifact = self.load_artifact(run_id)
        return [group.to_dict() for group in artifact.groups] if artifact and artifact.groups else []

    def group_details(self, run_id: int, group_id: str) -> dict[str, Any] | None:
        artifact = self.load_artifact(run_id)
        if artifact is None:
            return None
        group = next((g for g in artifact.groups or [] if g.id == group_id), None)
        if group is None:
            return None
        evidence_by_id = {item.id: item for item in artifact.evidence}
        source_by_id = {profile.id: profile for profile in artifact.sources}
        details = group.to_dict()
        details["member_details"] = [
            {
                "evidence": evidence_by_id[member_id].to_dict(),
                "source": source_by_id[evidence_by_id[member_id].source_id].to_dict()
                if evidence_by_id[member_id].source_id in source_by_id
                else None,
            }
            for member_id in group.members
            if member_id in evidence_by_id
        ]
        return details

    def pending_group_details(self, research_id: str) -> list[dict[str, Any]]:
        run = self.latest_run(research_id)
        if run is None:
            return []
        details: list[dict[str, Any]] = []
        for group in self.groups_for_run(int(run["run_id"])):
            if group.get("effective_status") in {"conflict_pending", "insufficient_pending"}:
                full = self.group_details(int(run["run_id"]), group["id"])
                if full:
                    details.append(full)
        return details

    # ---------------------------------------------------------------- reviews

    def add_review(
        self,
        research_id: str,
        group_id: str,
        action: str,
        *,
        representative_source_id: str | None = None,
        reviewer: str | None = None,
    ) -> dict[str, Any]:
        if action not in REVIEW_ACTIONS:
            raise ValueError(f"unsupported review action: {action}")
        run = self.latest_run(research_id)
        if run is None:
            raise KeyError(f"research {research_id} not found")
        group = self.group_details(int(run["run_id"]), group_id)
        if group is None:
            raise KeyError(f"group {group_id} not found for research {research_id}")
        if action == "set_representative" and not representative_source_id:
            raise ValueError("set_representative requires representative_source_id")
        created_at = _now_iso()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT INTO reviews (research_id, group_id, action,"
                " representative_source_id, reviewer, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    research_id,
                    group_id,
                    action,
                    representative_source_id,
                    reviewer,
                    created_at,
                ),
            )
        return {
            "review_id": int(cursor.lastrowid),
            "research_id": research_id,
            "group_id": group_id,
            "action": action,
            "representative_source_id": representative_source_id,
            "reviewer": reviewer,
            "created_at": created_at,
        }

    def reviews_for_run(self, research_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM reviews WHERE research_id = ? ORDER BY review_id ASC",
            (research_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def _latest_reviews(self, research_id: str) -> dict[str, dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM reviews WHERE research_id = ? ORDER BY review_id ASC",
            (research_id,),
        ).fetchall()
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            review = dict(row)
            latest[review["group_id"]] = {
                "action": review["action"],
                "representative_source_id": review["representative_source_id"],
                "reviewer": review["reviewer"],
                "reviewed_at": review["created_at"],
            }
        return latest

    # ------------------------------------------------ judgement cache (LLM)

    def get(self, kind: str, key: str) -> Any:
        row = self._conn.execute(
            "SELECT result_json FROM judgements WHERE kind = ? AND key = ?",
            (kind, key),
        ).fetchone()
        return _loads(row["result_json"], None) if row else None

    def set(self, kind: str, key: str, value: Any) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO judgements (kind, key, result_json, created_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(kind, key) DO UPDATE SET result_json = excluded.result_json,"
                " created_at = excluded.created_at",
                (kind, key, _dumps(value), _now_iso()),
            )

    # ------------------------------------------------------ extraction cache

    @staticmethod
    def content_hash(content: str) -> str:
        return hashlib.sha256((content or "").encode("utf-8", "replace")).hexdigest()

    def get_extraction(self, url: str, content_hash: str) -> list[dict[str, Any]] | None:
        row = self._conn.execute(
            "SELECT content_hash, payload_json FROM extraction_cache WHERE url = ?",
            (url,),
        ).fetchone()
        if row is None or row["content_hash"] != content_hash:
            return None
        payload = _loads(row["payload_json"], None)
        return payload if isinstance(payload, list) else None

    def put_extraction(self, url: str, content_hash: str, items: list[dict[str, Any]]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO extraction_cache (url, content_hash, payload_json, updated_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(url) DO UPDATE SET content_hash = excluded.content_hash,"
                " payload_json = excluded.payload_json, updated_at = excluded.updated_at",
                (url, content_hash, _dumps(items), _now_iso()),
            )



