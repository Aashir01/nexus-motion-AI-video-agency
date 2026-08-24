"""Coaxing strict JSON out of models that don't guarantee it."""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """Best-effort JSON recovery from a model response.

    Handles fenced blocks, leading prose, trailing commentary and the classic
    trailing-comma slip. Raises ValueError when nothing parses.
    """
    if text is None:
        raise ValueError("empty response")
    raw = text.strip()
    if not raw:
        raise ValueError("empty response")

    candidates: list[str] = []
    fenced = _FENCE.findall(raw)
    candidates.extend(block.strip() for block in fenced)
    candidates.append(raw)

    for opener, closer in (("{", "}"), ("[", "]")):
        start = raw.find(opener)
        end = raw.rfind(closer)
        if start != -1 and end > start:
            candidates.append(raw[start : end + 1])

    for candidate in candidates:
        for attempt in (candidate, _strip_trailing_commas(candidate)):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                continue

    raise ValueError(f"no parsable JSON in response (first 300 chars): {raw[:300]}")


def _strip_trailing_commas(s: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", s)


def schema_instructions(schema: dict) -> str:
    """A compact instruction block for providers without native schema support."""
    return (
        "Respond with a single JSON value and nothing else — no prose, no markdown "
        "fence, no commentary. It must validate against this JSON Schema:\n"
        f"{json.dumps(schema, separators=(',', ':'))}"
    )
