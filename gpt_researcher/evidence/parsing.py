"""Recovery of JSON payloads from dirty LLM output (fenced/prose/malformed)."""

import re
from typing import Any, Iterator

import json_repair

JSON_BLOCK_PATTERNS = (
    re.compile(r"```(?:json)?\s*(?P<payload>[\s\S]*?)```", re.IGNORECASE),
    re.compile(r"(?P<payload>\[[\s\S]*\])"),
    re.compile(r"(?P<payload>\{[\s\S]*\})"),
)


def iter_parsed_candidates(response: str) -> Iterator[Any]:
    """Yield repaired JSON values from the whole response and fenced blocks."""
    candidates: list[str] = []
    text = (response or "").strip()
    if text:
        candidates.append(text)
    for pattern in JSON_BLOCK_PATTERNS:
        for match in pattern.finditer(response or ""):
            candidate = match.group("payload").strip()
            if candidate and candidate not in candidates:
                candidates.append(candidate)

    for candidate in candidates:
        try:
            parsed = json_repair.loads(candidate)
        except Exception:
            continue
        if parsed is not None:
            yield parsed


def parse_json_object(response: str) -> dict:
    """First dict payload in the response, or ``{}`` when none parses."""
    for parsed in iter_parsed_candidates(response):
        if isinstance(parsed, dict):
            return parsed
    return {}
