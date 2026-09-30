"""Review loop routes (ticket 12): pending groups, review actions, regeneration.

Only exception groups (conflict/insufficient pending) are exposed; review
actions never rewrite the original verdict — they are stored as review records
that feed the *effective* conclusion used by report regeneration. Regeneration
re-runs the writing stage only (no retrieval/scraping), writes a new versioned
report file set and records the version in the SQLite store.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from gpt_researcher.actions.report_generation import generate_report
from gpt_researcher.config import Config
from gpt_researcher.evidence import (
    EvidenceStore,
    build_plan_for_config,
    finalize_report,
)
from gpt_researcher.prompts import PromptFamily, get_prompt_family
from gpt_researcher.utils.enum import Tone

try:
    from backend.utils import write_md_to_pdf, write_md_to_word, write_text_to_md
except ImportError:  # pragma: no cover - legacy sys.path-shimmed import
    from utils import write_md_to_pdf, write_md_to_word, write_text_to_md

logger = logging.getLogger(__name__)

router = APIRouter()

GenerateReport = Callable[[str, str], Awaitable[str]]


def _db_path() -> str:
    return os.getenv("EVIDENCE_DB_PATH", "data/evidence.db")


def open_store() -> EvidenceStore:
    return EvidenceStore(_db_path())


def _safe_name(research_id: str) -> str:
    return re.sub(r"[^\w.\-]", "_", research_id or "research")[:80]


async def _default_generate(config: Config) -> GenerateReport:
    """Writing-stage LLM call: evidence context -> report markdown.

    Mirrors the basic report body-writing path (role/prompt family from the
    configuration); introduction/conclusion were never part of the body and
    are not re-run. Retrieval/scraping never happens here.
    """
    prompt_family = get_prompt_family(
        getattr(config, "prompt_family", "default"), config
    )

    async def generate(query: str, context: str) -> str:
        return await generate_report(
            query=query,
            context=context,
            agent_role_prompt=(
                getattr(config, "agent_role", None)
                or "你是市场调研分析师，只依据给定证据撰写可靠的中文报告。"
            ),
            report_type="research_report",
            tone=Tone.Objective,
            report_source="web",
            websocket=None,
            cfg=config,
            prompt_family=prompt_family,
        )

    return generate


async def _save_version_files(
    markdown: str, research_id: str, version: int
) -> dict[str, str]:
    base = f"{_safe_name(research_id)}.v{version}"
    md_path = await write_text_to_md(markdown, base)
    docx_path = await write_md_to_word(markdown, base)
    pdf_path = await write_md_to_pdf(markdown, base)
    return {"md": md_path, "docx": docx_path, "pdf": pdf_path}


async def regenerate_report(
    research_id: str,
    *,
    store: EvidenceStore | None = None,
    generate: GenerateReport | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Re-run the writing stage for the latest run, producing a new version."""
    own_store = store is None
    store = store or open_store()
    try:
        run = store.latest_run(research_id)
        if run is None:
            raise KeyError(f"research {research_id} not found")
        artifact = store.load_artifact(int(run["run_id"]))
        if artifact is None:
            raise KeyError(f"run {run['run_id']} not found")
        config = config or Config()
        plan = build_plan_for_config(artifact, config)
        if plan.blocked:
            return {"success": False, "blocked": plan.blocked_message}

        generate = generate or await _default_generate(config)
        draft = await generate(artifact.query, plan.context)
        report_markdown = finalize_report(draft, plan)

        version = store.next_version(research_id)
        paths = await _save_version_files(report_markdown, research_id, version)
        run_id = store.record_run(artifact, version=version, report_paths=paths)
        # The evidence snapshot is re-exported from the store so the download
        # carries the review records that produced this version (ticket 23).
        evidence_paths = store.export_artifact(run_id)
        return {
            "success": True,
            "version": version,
            "run_id": run_id,
            "report": report_markdown,
            "paths": paths,
            "evidence": evidence_paths,
        }
    finally:
        if own_store:
            store.close()


@router.get("/review/{research_id}", response_class=HTMLResponse)
async def review_page(research_id: str) -> HTMLResponse:
    html = _PAGE_HTML.replace("__RESEARCH_ID__", json.dumps(research_id))
    return HTMLResponse(content=html)


@router.get("/api/research/{research_id}/evidence")
async def evidence_overview(research_id: str) -> dict[str, Any]:
    store = open_store()
    try:
        run = store.latest_run(research_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Research not found")
        artifact = store.load_artifact(int(run["run_id"]))
        if artifact is None:
            raise HTTPException(status_code=404, detail="Research run not found")
        return {
            "research_id": research_id,
            "query": artifact.query,
            "summary": artifact.summary,
            "versions": store.list_runs(research_id),
            "pending_groups": store.pending_group_details(research_id),
        }
    finally:
        store.close()


@router.post("/api/research/{research_id}/reviews")
async def submit_review(research_id: str, request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid review payload")

    group_id = str(payload.get("group_id") or "").strip()
    action = str(payload.get("action") or "").strip()
    if not group_id or not action:
        raise HTTPException(status_code=400, detail="group_id and action are required")

    store = open_store()
    try:
        review = store.add_review(
            research_id,
            group_id,
            action,
            representative_source_id=payload.get("representative_source_id") or None,
            reviewer=payload.get("reviewer") or None,
        )
        run = store.latest_run(research_id)
        group = None
        if run is not None:
            group = store.group_details(int(run["run_id"]), group_id)
            # Refresh the downloadable snapshot so it carries the review
            # records (证据组 + 裁决结论 + 复核记录, ticket 23).
            try:
                store.export_artifact(int(run["run_id"]))
            except Exception as exc:
                logger.warning("Failed to re-export evidence snapshot: %s", exc)
        return {"success": True, "review": review, "group": group}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        store.close()


@router.post("/api/research/{research_id}/regenerate")
async def regenerate(research_id: str) -> dict[str, Any]:
    try:
        result = await regenerate_report(research_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if not result.get("success"):
        raise HTTPException(status_code=409, detail=result.get("blocked"))
    return result


_PAGE_HTML = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>证据复核</title>
<style>
body { font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; margin: 24px; color: #1f2937; }
h1 { font-size: 20px; } h2 { font-size: 16px; margin-top: 28px; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 13px; }
th, td { border: 1px solid #d1d5db; padding: 6px 8px; text-align: left; vertical-align: top; }
th { background: #f3f4f6; }
.group { border: 1px solid #d1d5db; border-radius: 8px; padding: 12px; margin: 12px 0; }
.actions { margin-top: 8px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
button { padding: 6px 12px; border-radius: 6px; border: 1px solid #2563eb; background: #2563eb; color: #fff; cursor: pointer; }
button.secondary { background: #fff; color: #2563eb; }
button.danger { border-color: #dc2626; background: #dc2626; }
select, input { padding: 5px 8px; border: 1px solid #d1d5db; border-radius: 6px; }
.muted { color: #6b7280; font-size: 12px; }
#msg { margin: 10px 0; font-weight: 600; }
pre { background: #f9fafb; padding: 10px; border-radius: 6px; overflow-x: auto; }
.status { font-weight: 600; }
</style>
</head>
<body>
<h1>证据复核 · <span class="muted" id="rid"></span></h1>
<div id="msg"></div>
<h2>待审证据组（仅例外项）</h2>
<div id="groups" class="muted">加载中…</div>
<h2>版本记录</h2>
<pre id="versions" class="muted">加载中…</pre>
<script>
const rid = __RESEARCH_ID__;
const $ = (id) => document.getElementById(id);

function esc(text) {
  return String(text == null ? "" : text).replace(/[&<>"']/g,
    (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

async function load() {
  $("rid").textContent = rid;
  const response = await fetch(`/api/research/${encodeURIComponent(rid)}/evidence`);
  if (!response.ok) { $("groups").textContent = "未找到该调研（" + response.status + "）"; return; }
  const data = await response.json();
  const summary = data.summary || {};
  $("versions").textContent = JSON.stringify(data.versions || [], null, 2);
  const pending = data.pending_groups || [];
  if (!pending.length) {
    $("groups").innerHTML = '<p class="muted">没有待审证据组，全部数据已处置。</p>';
    return;
  }
  $("groups").innerHTML = pending.map(renderGroup).join("");
}

function renderGroup(group) {
  const members = group.member_details || [];
  const options = members.map((m) =>
    `<option value="${esc(m.source.id)}">${esc(m.source.publisher || m.source.domain)}（${esc(m.source.tier)}）</option>`
  ).join("");
  const rows = members.map((m) => {
    const e = m.evidence || {}, s = m.source || {};
    return `<tr><td>${esc(s.id)}</td><td>${esc(s.tier)}</td>
      <td>${esc(s.publisher || s.domain)}</td>
      <td>${esc(e.value_raw || e.value)}</td><td>${esc(e.scope)}</td>
      <td>${esc(e.quote)}</td><td><a href="${esc(s.url)}" target="_blank" rel="noreferrer">链接</a></td></tr>`;
  }).join("");
  return `<div class="group">
    <div><b>${esc(group.id)}</b> ${esc((group.key||{}).metric || "")}
      <span class="status">${esc(group.effective_status)}</span></div>
    <div class="muted">${esc((group.verdict||{}).reason || "")}</div>
    <table>
      <tr><th>来源</th><th>等级</th><th>发布主体</th><th>数值</th><th>口径</th><th>原文引用</th><th>URL</th></tr>
      ${rows}
    </table>
    <div class="actions">
      <input placeholder="复核人" id="reviewer-${esc(group.id)}">
      <button onclick="review('${esc(group.id)}','accept')">采纳</button>
      <button class="danger" onclick="review('${esc(group.id)}','reject')">否决</button>
      <select id="rep-${esc(group.id)}">${options}</select>
      <button class="secondary" onclick="setRepresentative('${esc(group.id)}')">指定主来源</button>
      <button class="secondary" onclick="regenerate()">重生成报告</button>
    </div>
  </div>`;
}

function reviewerOf(groupId) {
  const el = $("reviewer-" + groupId);
  return el ? el.value : "";
}

async function review(groupId, action) {
  const body = { group_id: groupId, action, reviewer: reviewerOf(groupId) };
  const response = await fetch(`/api/research/${encodeURIComponent(rid)}/reviews`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
  });
  const data = await response.json().catch(() => ({}));
  $("msg").textContent = response.ok ? `已${action === "accept" ? "采纳" : "否决"} ${groupId}` : ("失败：" + (data.detail || response.status));
  await load();
}

async function setRepresentative(groupId) {
  const select = $("rep-" + groupId);
  const body = { group_id: groupId, action: "set_representative",
                 representative_source_id: select ? select.value : "",
                 reviewer: reviewerOf(groupId) };
  const response = await fetch(`/api/research/${encodeURIComponent(rid)}/reviews`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
  });
  const data = await response.json().catch(() => ({}));
  $("msg").textContent = response.ok ? `已指定主来源 ${groupId}` : ("失败：" + (data.detail || response.status));
  await load();
}

async function regenerate() {
  $("msg").textContent = "正在重生成（只重跑写作阶段）…";
  const response = await fetch(`/api/research/${encodeURIComponent(rid)}/regenerate`, { method: "POST" });
  const data = await response.json().catch(() => ({}));
  $("msg").textContent = response.ok
    ? `已生成 v${data.version}：` + JSON.stringify(data.paths)
    : ("重生成失败：" + (data.detail || response.status));
  await load();
}

load();
</script>
</body>
</html>
"""
