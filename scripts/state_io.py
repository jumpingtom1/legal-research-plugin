"""Shared utilities for legal research scripts: state I/O and excerpt normalization."""

import json


def load_state(path):
    """Load a research state JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(path, state):
    """Save a research state dict to a JSON file."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def normalize_excerpts(case):
    """Normalize excerpts from a case dict into a list of {text} dicts.

    Handles both field names ("key_excerpts" and "excerpts") and both formats
    (plain strings and dicts with a "text" key).
    """
    raw = case.get("key_excerpts") or case.get("excerpts") or []
    normalized = []
    for ex in raw:
        if isinstance(ex, str):
            normalized.append({"text": ex})
        elif isinstance(ex, dict):
            normalized.append({"text": ex.get("text", "")})
    return normalized
