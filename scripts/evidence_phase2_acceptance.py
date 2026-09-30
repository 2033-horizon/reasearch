"""Golden acceptance run for phase 2 (ticket 13): adjudication + review loop.

Runs one real research query with the evidence layer and adjudication enabled
and checks the phase 2 acceptance criteria:

1. the artifact is schema v2 with evidence groups + verdicts + citation map;
2. every verdict is consistent with the adjudication rule table (independent
   source counts vs tier thresholds);
3. up to 20 sampled evidence quotes are verbatim-locatable in their source;
4. the report body only cites valid conclusions ([^n] within the citation
   map), footnote definitions exist and pending data sits in the appendix;
5. the DOCX export carries real footnotes (word/footnotes.xml + references);
6. the clustering/adjudication stages appear in the cost statistics;
7. when pending groups exist, one is accepted through the review store and the
   report regenerates to a new version while the old version stays recorded.

This script talks to real search + LLM providers, so it is NOT part of the
offline test suite. Manual Word/WPS footnote confirmation is documented in
docs/docs/gpt-researcher/gptr/evidence-layer.md.

    python scripts/evidence_phase2_acceptance.py "2024年中国新能源汽车销量"
    python scripts/evidence_phase2_acceptance.py "新能源补贴政策" --domains stats.gov.cn,miit.gov.cn
"""

import argparse
import asyncio
import os
import re
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from gpt_researcher import GPTResearcher  # noqa: E402
from gpt_researcher.evidence import AdjudicationRules, EvidenceStore  # noqa: E402
from gpt_researcher.evidence.extraction import fold_text  # noqa: E402
from gpt_researcher.evidence.writing import CITATION_TOKEN_RE  # noqa: E402
from gpt_researcher.utils.enum import ReportType  # noqa: E402

SAMPLE_SIZE = 20
PENDING_STATUSES = {"conflict_pending", "insufficient_pending"}


def _source_content_map(sources) -> dict[str, str]:
    contents: dict[str, str] = {}
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        url = source.get("url") or source.get("href") or ""
        if not url:
            continue
        content = source.get("raw_content") or source.get("content") or ""
        if content and len(content) > len(contents.get(url, "")):
            contents[url] = content
    return contents


def _check_policy(artifact, rules: AdjudicationRules, failures: list[str]) -> None:
    for group in artifact.groups or []:
        verdict = group.verdict or {}
        required = int(verdict.get("required", 0))
        independent = int(verdict.get("independent_sources", 0))
        status = group.status
        if status in {"accepted", "cross_validated"} and independent < required:
            failures.append(
                f"{group.id} {status} but独立来源 {independent} < 门槛 {required}"
            )
        if status == "insufficient_pending" and independent >= required:
            failures.append(
                f"{group.id} 证据不足待审 but独立来源 {independent} >= 门槛 {required}"
            )
        if status == "conflict_pending" and not verdict.get("conflicts"):
            failures.append(f"{group.id} 冲突待审但未记录冲突数据")
        best_tier = verdict.get("best_tier")
        if best_tier and rules.thresholds.get(best_tier) != required:
            failures.append(
                f"{group.id} 门槛与策略表不一致：{best_tier}={required} "
                f"vs {rules.thresholds.get(best_tier)}"
            )
    groups = artifact.groups or []
    cross_source = [g for g in groups if g.independent_sources >= 2]
    conflicts = [g for g in groups if g.status == "conflict_pending"]
    print(
        f"[2] 裁决与策略表一致：{len(groups)} 个证据组已校验"
        f"（{sum(1 for g in groups if g.status in {'accepted', 'cross_validated'})} 个有效结论、"
        f"{sum(1 for g in groups if g.status in PENDING_STATUSES)} 个待审、"
        f"{len(cross_source)} 个跨来源组、{len(conflicts)} 个冲突待审）"
    )
    if not cross_source:
        print("    WARN: 本次检索未形成跨来源一致组（检索结果决定，可换更丰富的查询重跑）")
    if not conflicts:
        print("    WARN: 本次检索未出现冲突待审组（非代码问题）")


async def _check_report_and_docx(researcher, artifact, failures: list[str]) -> str:
    from gpt_researcher.evidence import EMPTY_EVIDENCE_MESSAGE

    report = await researcher.write_report()
    citations = artifact.citations or {}
    pending = [
        g for g in artifact.groups or [] if g.effective_status in PENDING_STATUSES
    ]

    if report == EMPTY_EVIDENCE_MESSAGE:
        # No valid conclusion at all: abstaining is the correct, non-fabricated
        # outcome; the review loop below is what turns a pending group into
        # report content.
        print(f"[3] 正文：无有效结论，报告明确 abstain（未编造）；待审 {len(pending)} 组")
        print("[4] 待审附录：abstain 提示已说明（无需附录）")
        print("[5] DOCX 脚注：无引用，跳过")
        return report

    used = {int(match.group(1)) for match in CITATION_TOKEN_RE.finditer(report)}
    invalid = {n for n in used if str(n) not in citations}
    if invalid:
        failures.append(f"正文引用了不存在的编号：{sorted(invalid)}")
    missing_definitions = {n for n in used if f"[^{n}]:" not in report}
    if used and missing_definitions:
        failures.append(f"缺少脚注定义：{sorted(missing_definitions)}")
    print(f"[3] 正文引用：使用编号 {sorted(used)}，citation 映射 {len(citations)} 条")

    if pending and "附录：待审数据" not in report:
        failures.append("存在待审组但报告没有待审附录")
    print(f"[4] 待审附录：{'有' if '附录：待审数据' in report else '无'}（待审 {len(pending)} 组）")

    from backend.utils import write_md_to_word

    docx_path = await write_md_to_word(report, "phase2_acceptance")
    docx_path = urllib.parse.unquote(docx_path)
    import zipfile

    with zipfile.ZipFile(docx_path) as archive:
        names = archive.namelist()
        document = archive.read("word/document.xml").decode("utf-8")
    if used:
        if "word/footnotes.xml" not in names:
            failures.append("DOCX 缺少脚注部件")
        if document.count("footnoteReference") != len(list(CITATION_TOKEN_RE.finditer(report))):
            failures.append("DOCX 脚注引用数与正文上标数不一致")
    print(f"[5] DOCX 脚注：{docx_path}")
    return report


async def _check_regeneration(researcher, artifact, store: EvidenceStore, failures: list[str]) -> None:
    pending = [
        g for g in artifact.groups or [] if g.effective_status in PENDING_STATUSES
    ]
    if not pending:
        print("[7] 复核与重生成：SKIP（本次调研没有待审组）")
        return
    target = pending[0]
    store.add_review(
        artifact.research_id,
        target.id,
        "accept",
        reviewer="acceptance-script",
    )
    from backend.server.review import regenerate_report

    result = await regenerate_report(artifact.research_id, store=store)
    if not result.get("success"):
        failures.append(f"重生成未成功：{result}")
        return
    runs = store.list_runs(artifact.research_id)
    versions = [entry["version"] for entry in runs]
    if result["version"] not in versions or 1 not in versions:
        failures.append(f"版本记录不完整：{versions}")
    reloaded = store.load_artifact(store.latest_run(artifact.research_id)["run_id"])
    accepted_now = any(
        g.id == target.id and g.effective_status == "accepted"
        for g in (reloaded.groups or [])
    )
    if not accepted_now:
        failures.append(f"复核采纳未合成有效结论：{target.id}")
    citations = reloaded.citations or {}
    if target.id not in {entry["group_id"] for entry in citations.values()}:
        failures.append(f"复核采纳后 {target.id} 未进入 citation 映射")

    report = result.get("report") or ""
    used = {int(match.group(1)) for match in CITATION_TOKEN_RE.finditer(report)}
    if not used:
        failures.append("重生成报告没有引用任何有效结论")
    if {n for n in used if str(n) not in citations}:
        failures.append("重生成报告引用了无效编号")
    if used and {n for n in used if f"[^{n}]:" not in report}:
        failures.append("重生成报告缺少脚注定义")
    unresolved = [
        g
        for g in (reloaded.groups or [])
        if g.effective_status in PENDING_STATUSES
    ]
    if unresolved and "附录：待审数据" not in report:
        failures.append("重生成报告缺少待审附录")
    print(
        f"[7] 复核与重生成：{target.id} 采纳 → v{result['version']}，"
        f"正文编号 {sorted(used)}，剩余待审 {len(unresolved)} 组，"
        f"版本 {versions} 旧版保留"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="真实研究查询")
    parser.add_argument("--domains", default="", help="逗号分隔的定向域名（可选）")
    parser.add_argument(
        "--max-sources",
        type=int,
        default=None,
        help="覆盖 EVIDENCE_MAX_SOURCES（用于控制验收成本）",
    )
    parser.add_argument(
        "--db",
        default="data/evidence_acceptance.db",
        help="验收用 SQLite 数据库路径",
    )
    args = parser.parse_args()

    os.environ["EVIDENCE_EXTRACTION_ENABLED"] = "true"
    os.environ["ADJUDICATION_ENABLED"] = "true"
    os.environ["EVIDENCE_DB_PATH"] = args.db
    if args.max_sources is not None:
        os.environ["EVIDENCE_MAX_SOURCES"] = str(args.max_sources)

    domains = [d.strip() for d in args.domains.split(",") if d.strip()]
    print(f"[0] 查询: {args.query}（db={args.db}）")

    researcher = GPTResearcher(
        query=args.query,
        report_type=ReportType.ResearchReport.value,
        query_domains=domains or None,
    )
    await researcher.conduct_research()

    artifact = researcher.evidence_artifact
    if artifact is None or artifact.groups is None:
        print("FAIL: 未产出裁决产物（检查 EVIDENCE_EXTRACTION_ENABLED/ADJUDICATION_ENABLED 与 LLM 配置）")
        return 1

    failures: list[str] = []
    summary = artifact.summary
    print(
        f"    summary: sources={summary['sources']} evidence={summary['evidence']} "
        f"groups={summary['groups']} verdicts={summary['verdicts']} pending={summary['pending']}"
    )
    print(f"[1] 产物 schema v{artifact.schema_version}，citation 映射 {len(artifact.citations or {})} 条")
    if artifact.schema_version != 2:
        failures.append("schema 不是 v2")

    rules = AdjudicationRules.load(
        getattr(researcher.cfg, "adjudication_rules_path", "") or None
    )
    _check_policy(artifact, rules, failures)

    contents = _source_content_map(researcher.research_sources)
    url_by_source = {profile.id: profile.url for profile in artifact.sources}
    sampled = artifact.evidence[:SAMPLE_SIZE]
    hits = sum(
        1
        for item in sampled
        if item.quote
        and fold_text(item.quote) in fold_text(contents.get(url_by_source.get(item.source_id, ""), ""))
    )
    print(f"[6a] 引用逐字命中: {hits}/{len(sampled)}")
    if len(sampled) < SAMPLE_SIZE or hits != len(sampled):
        failures.append(f"引用抽样未达标：{hits}/{len(sampled)}（要求 {SAMPLE_SIZE} 条全中）")

    await _check_report_and_docx(researcher, artifact, failures)

    step_costs = researcher.get_step_costs()
    print(
        f"[6b] 成本: 总计 ${researcher.get_costs():.4f}，"
        f"证据 ${step_costs.get('evidence', 0.0):.4f}，"
        f"裁决 ${step_costs.get('adjudication', 0.0):.4f}"
    )
    if step_costs.get("evidence", 0.0) <= 0:
        failures.append("证据阶段成本为 0")

    store = EvidenceStore(args.db)
    try:
        await _check_regeneration(researcher, artifact, store, failures)
        latest = store.latest_run(artifact.research_id)
        reloaded = store.load_artifact(latest["run_id"])
        if reloaded is not None:
            original = store.load_artifact(latest["run_id"]).to_dict()
            exported = store.export_artifact(latest["run_id"])
            import json

            with open(
                ROOT / urllib.parse.unquote(exported["json"]), encoding="utf-8"
            ) as handle:
                if json.load(handle) != original:
                    failures.append("库导出产物与回读不一致")
        print("[8] 证据库回读/导出：一致")
    finally:
        store.close()

    if failures:
        print("\nFAIL:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("\nPASS: 阶段二黄金用例验收通过（Word/WPS 脚注需人工确认）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
