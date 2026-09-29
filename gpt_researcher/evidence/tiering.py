"""Source tiering: rule table, domain matching, LLM fallback and cache.

Priority per ADR-0002: ``domain_exact`` > ``suffix`` (longest wins) >
LLM fallback (capped at C) > default D. The rule table is data (YAML) so
business can maintain whitelists without code changes.
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

import json_repair
import yaml

from ..utils.domains import normalize_domain

logger = logging.getLogger(__name__)

DEFAULT_RULES_PATH = Path(__file__).with_name("rules") / "default_rules.yaml"
DEFAULT_TIER = "D"
LLM_MAX_TIER = "C"
CACHE_VERSION = 1

LLMCall = Callable[[str], Awaitable[str]]


@dataclass
class TierRule:
    match: str  # "domain_exact" | "suffix"
    value: str
    tier: str
    publisher: str = ""
    org_type: str = ""

    @property
    def label(self) -> str:
        return f"{self.match}:{self.value}"


class TierRules:
    """Domain -> tier mapping loaded from a YAML data file."""

    def __init__(self, rules: list[TierRule]):
        self.rules = rules
        self._exact: dict[str, TierRule] = {}
        self._suffixes: list[tuple[str, TierRule]] = []
        for rule in rules:
            if rule.match == "domain_exact":
                self._exact.setdefault(rule.value, rule)
            elif rule.match == "suffix":
                suffix = rule.value if rule.value.startswith(".") else f".{rule.value}"
                self._suffixes.append((suffix, rule))
        # Longest suffix wins (e.g. `.stats.gov.cn` beats `.gov.cn`).
        self._suffixes.sort(key=lambda pair: len(pair[0]), reverse=True)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "TierRules":
        rule_path = Path(path) if path else DEFAULT_RULES_PATH
        if not rule_path.exists():
            if path:
                logger.warning(
                    "Reliability rules not found at %s; falling back to packaged defaults.",
                    rule_path,
                )
                rule_path = DEFAULT_RULES_PATH
            else:
                logger.error("Packaged reliability rules missing at %s", rule_path)
                return cls([])

        try:
            with open(rule_path, "r", encoding="utf-8") as handle:
                payload = yaml.safe_load(handle) or {}
        except Exception as exc:
            logger.error("Failed to load reliability rules from %s: %s", rule_path, exc)
            return cls([]) if rule_path == DEFAULT_RULES_PATH else cls.load(None)

        raw_rules = payload.get("rules") if isinstance(payload, dict) else None
        rules: list[TierRule] = []
        for raw in raw_rules or []:
            if not isinstance(raw, dict):
                continue
            match = str(raw.get("match") or "").strip()
            tier = str(raw.get("tier") or "").strip().upper()
            if match == "domain_exact":
                value = normalize_domain(raw.get("value"))
            elif match == "suffix":
                value = normalize_domain(str(raw.get("value") or ""))
                if value and not value.startswith("."):
                    value = f".{value}"
            else:
                continue
            if not value or tier not in {"A", "B", "C", "D"}:
                continue
            rules.append(
                TierRule(
                    match=match,
                    value=value,
                    tier=tier,
                    publisher=str(raw.get("publisher") or ""),
                    org_type=str(raw.get("org_type") or ""),
                )
            )
        return cls(rules)

    def match(self, host: str) -> tuple[TierRule | None, str | None]:
        """Return the winning rule and its label for a normalized host."""
        if not host:
            return None, None
        exact = self._exact.get(host)
        if exact is not None:
            return exact, exact.label
        for suffix, rule in self._suffixes:
            if host == suffix[1:] or host.endswith(suffix):
                return rule, rule.label
        return None, None


@dataclass
class TierAssignment:
    tier: str
    assigned_by: str  # "rule" | "llm" | "default"
    publisher: str | None = None
    org_type: str | None = None
    matched_rule: str | None = None


def build_tier_prompt(domain: str, title: str = "") -> str:
    return (
        "你是市场调研的来源分级助手（来源分级）。根据域名与页面标题判断发布主体类型。\n"
        "只输出 JSON，不要输出其他内容：\n"
        '{"tier": "C" 或 "D", "publisher": "发布主体名称", "org_type": '
        '"government|international_org|association|institute|brokerage|consulting|academia|media|central_media|portal|ugc"}\n'
        "规则：无法确认是权威机构、央媒等可靠来源时，必须给 D；只有行业机构或正规媒体可给 C。\n"
        f"域名：{domain}\n"
        f"页面标题：{title or '（无）'}\n"
    )


def _parse_json_object(response: str) -> dict:
    candidates = [response.strip()]
    import re

    for pattern in (
        re.compile(r"```(?:json)?\s*(?P<payload>[\s\S]*?)```", re.IGNORECASE),
        re.compile(r"(?P<payload>\{[\s\S]*\})"),
    ):
        for match in pattern.finditer(response):
            candidate = match.group("payload").strip()
            if candidate and candidate not in candidates:
                candidates.append(candidate)
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json_repair.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


class TierClassifier:
    """Assigns a tier to a URL: rules first, LLM fallback capped at C, else D."""

    def __init__(
        self,
        rules: TierRules,
        llm: LLMCall | None = None,
        cache_path: str | None = None,
    ):
        self.rules = rules
        self.llm = llm
        self.cache_path = cache_path or None
        self._cache: dict[str, dict] = self._load_cache()

    def _load_cache(self) -> dict[str, dict]:
        if not self.cache_path or not os.path.exists(self.cache_path):
            return {}
        try:
            with open(self.cache_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            logger.warning("Failed to read tier cache %s: %s", self.cache_path, exc)
            return {}
        domains = payload.get("domains") if isinstance(payload, dict) else None
        return domains if isinstance(domains, dict) else {}

    def _save_cache(self) -> None:
        if not self.cache_path:
            return
        try:
            parent = os.path.dirname(self.cache_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {"version": CACHE_VERSION, "domains": self._cache},
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as exc:
            logger.warning("Failed to persist tier cache %s: %s", self.cache_path, exc)

    async def classify(self, url: str, title: str = "") -> TierAssignment:
        host = normalize_domain(url)
        if host:
            rule, label = self.rules.match(host)
            if rule is not None:
                return TierAssignment(
                    tier=rule.tier,
                    assigned_by="rule",
                    publisher=rule.publisher or None,
                    org_type=rule.org_type or None,
                    matched_rule=label,
                )

        domain = host or (url or "").strip()
        cached = self._cache.get(domain)
        if isinstance(cached, dict) and cached.get("tier") in {"C", "D"}:
            return TierAssignment(
                tier=cached["tier"],
                assigned_by="llm",
                publisher=cached.get("publisher") or None,
                org_type=cached.get("org_type") or None,
            )

        if self.llm is None:
            return TierAssignment(tier=DEFAULT_TIER, assigned_by="default")

        try:
            response = await self.llm(build_tier_prompt(domain, title))
            parsed = _parse_json_object(response)
            tier = str(parsed.get("tier") or "").strip().upper()
        except Exception as exc:
            logger.warning("LLM tier classification failed for %s: %s", domain, exc)
            return TierAssignment(tier=DEFAULT_TIER, assigned_by="default")

        if tier in {"A", "B"}:
            tier = LLM_MAX_TIER
        if tier not in {"C", "D"}:
            logger.warning("LLM returned unusable tier %r for %s; defaulting", tier, domain)
            return TierAssignment(tier=DEFAULT_TIER, assigned_by="default")

        publisher = str(parsed.get("publisher") or "").strip() or None
        org_type = str(parsed.get("org_type") or "").strip() or None
        if domain:
            self._cache[domain] = {
                "tier": tier,
                "publisher": publisher,
                "org_type": org_type,
                "assigned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            self._save_cache()
        return TierAssignment(
            tier=tier,
            assigned_by="llm",
            publisher=publisher,
            org_type=org_type,
        )
