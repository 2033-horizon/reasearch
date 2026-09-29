"""Golden acceptance run for the evidence layer (ticket 06).

Runs one real research query through GPT Researcher with
``EVIDENCE_EXTRACTION_ENABLED=true`` and checks the acceptance criteria:

1. the evidence artifact is written to ``outputs/`` as JSON + Markdown;
2. up to 20 sampled evidence quotes are verbatim-locatable in their source;
3. every source tier matches the rule table (rule hits) or is conservatively
   capped (``llm`` -> C/D, ``default`` -> D);
4. the extraction step is visible in the existing cost statistics;
5. optionally, ``--domains`` domain targeting reaches the retriever.

This script talks to real search + LLM providers, so it is NOT part of the
offline test suite; run it manually:

    python scripts/evidence_golden_acceptance.py "2025年中国新能源汽车销量"
    python scripts/evidence_golden_acceptance.py "..." --domains stats.gov.cn,miit.gov.cn
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from gpt_researcher import GPTResearcher  # noqa: E402
from gpt_researcher.evidence.extraction import fold_text  # noqa: E402
from gpt_researcher.evidence.tiering import TierRules  # noqa: E402
from gpt_researcher.utils.enum import ReportType  # noqa: E402

SAMPLE_SIZE = 20


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


def verify(artifact, researcher, rules: TierRules) -> list[str]:
    failures: list[str] = []

    contents = _source_content_map(researcher.research_sources)
    url_by_source = {profile.id: profile.url for profile in artifact.sources}

    sampled = artifact.evidence[:SAMPLE_SIZE]
    hits = 0
    for item in sampled:
        content = contents.get(url_by_source.get(item.source_id, ""), "")
        if item.quote and fold_text(item.quote) in fold_text(content):
            hits += 1
        else:
            failures.append(
                f"quote not found for {item.id} ({item.metric}): {item.quote[:50]!r}"
            )
    print(f"[2] 引用逐字命中: {hits}/{len(sampled)} (要求 100%)")
    if len(sampled) < SAMPLE_SIZE:
        failures.append(
            f"only {len(sampled)} evidence items available; cannot sample {SAMPLE_SIZE}"
        )

    for profile in artifact.sources:
        matched, label = rules.match(profile.domain)
        if profile.assigned_by == "rule":
            if matched is None or profile.matched_rule != label or profile.tier != matched.tier:
                failures.append(
                    f"rule mismatch for {profile.domain}: "
                    f"{profile.tier}/{profile.matched_rule} vs {matched.tier if matched else None}/{label}"
                )
        elif profile.assigned_by == "llm":
            if profile.tier not in {"C", "D"}:
                failures.append(f"LLM tier above C for {profile.domain}: {profile.tier}")
        elif profile.assigned_by == "default":
            if profile.tier != "D":
                failures.append(f"default tier must be D for {profile.domain}: {profile.tier}")
        else:
            failures.append(f"unknown assigned_by for {profile.domain}: {profile.assigned_by}")
    print(f"[3] 来源等级与判定方式: {len(artifact.sources)} 个来源已校验")

    paths = researcher.evidence_artifact_paths
    for key, path in (paths or {}).items():
        if not (ROOT / path).exists():
            failures.append(f"artifact path does not exist: {key}={path}")
    print(f"[1] 产物落盘: {paths}")

    step_costs = researcher.get_step_costs()
    evidence_cost = step_costs.get("evidence", 0.0)
    print(f"[4] 成本: 总计 ${researcher.get_costs():.4f}，证据抽取阶段 ${evidence_cost:.4f}")
    if artifact.evidence and evidence_cost <= 0:
        failures.append("evidence step cost is zero although evidence was extracted")

    return failures


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
    args = parser.parse_args()

    os.environ["EVIDENCE_EXTRACTION_ENABLED"] = "true"
    if args.max_sources is not None:
        os.environ["EVIDENCE_MAX_SOURCES"] = str(args.max_sources)

    domains = [d.strip() for d in args.domains.split(",") if d.strip()]
    print(f"[0] 查询: {args.query}")
    if domains:
        print(f"    定向域名: {domains}")

    researcher = GPTResearcher(
        query=args.query,
        report_type=ReportType.ResearchReport.value,
        query_domains=domains or None,
    )
    await researcher.conduct_research()

    artifact = researcher.evidence_artifact
    if artifact is None:
        print("FAIL: 证据产物未生成（检查 EVIDENCE_EXTRACTION_ENABLED 与 LLM 配置）")
        return 1

    summary = artifact.summary
    print(
        f"    summary: sources={summary['sources']} scraped={summary['sources_scraped']} "
        f"evidence={summary['evidence']} rejected={summary['rejected']} by_tier={summary['by_tier']}"
    )

    rules = TierRules.load(getattr(researcher.cfg, "reliability_rules_path", "") or None)
    failures = verify(artifact, researcher, rules)

    if failures:
        print("\nFAIL:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("\nPASS: 黄金用例验收通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
