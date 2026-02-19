#!/usr/bin/env python3
"""
Quote validation matcher for legal research excerpts.
Three-tier matching: normalized substring -> token sequence -> fuzzy sliding window.
Input: opinion text file path, excerpts JSON file path
Output: JSON array of per-excerpt results to stdout
"""

import sys
import json
import re
import unicodedata
import difflib


def normalize_text(text):
    """Normalize text for comparison: unicode, quotes, dashes, whitespace."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2014", "-").replace("\u2013", "-")
    text = text.replace("\ufb01", "fi").replace("\ufb02", "fl")
    text = text.replace("\u00a0", " ").replace("\u200b", "").replace("\u200c", "")
    text = text.replace("\ufeff", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokenize(text):
    """Extract lowercase alphanumeric word tokens."""
    return re.findall(r"[a-z0-9]+", text.lower())


def strip_brackets(text):
    """Remove bracketed editorial insertions like [A], [the], [emphasis added]."""
    return re.sub(r"\[[^\]]*\]", " ", text)


def split_ellipsis_segments(text):
    """Split excerpt at ellipsis boundaries (... or [...])."""
    text = re.sub(r"\[\s*\.\.\.\s*\]", "\x00", text)
    text = re.sub(r"\.{3,}", "\x00", text)
    segments = [s.strip() for s in text.split("\x00") if s.strip()]
    return segments


def find_token_subsequence(haystack_tokens, needle_tokens):
    """Find needle tokens as contiguous subsequence in haystack. Return start index or -1."""
    if not needle_tokens:
        return 0
    n = len(needle_tokens)
    for i in range(len(haystack_tokens) - n + 1):
        if haystack_tokens[i : i + n] == needle_tokens:
            return i
    return -1


def tier1_normalized_substring(norm_opinion, norm_excerpt):
    """Tier 1: Check if normalized excerpt is a substring of normalized opinion."""
    return norm_excerpt in norm_opinion


def tier2_token_sequence(opinion_tokens, excerpt_text):
    """Tier 2: Token-sequence match with ellipsis handling."""
    cleaned = strip_brackets(excerpt_text)
    segments = split_ellipsis_segments(cleaned)
    if not segments:
        return False
    segment_token_lists = [tokenize(seg) for seg in segments]
    segment_token_lists = [tl for tl in segment_token_lists if tl]
    if not segment_token_lists:
        return False
    last_end = 0
    for seg_tokens in segment_token_lists:
        search_space = opinion_tokens[last_end:]
        idx = find_token_subsequence(search_space, seg_tokens)
        if idx == -1:
            return False
        last_end = last_end + idx + len(seg_tokens)
    return True


def tier3_fuzzy_match(opinion_tokens, excerpt_text):
    """Tier 3: Sliding-window fuzzy match. Returns (best_ratio, best_window_text)."""
    cleaned = strip_brackets(excerpt_text)
    excerpt_tokens = tokenize(cleaned)
    if not excerpt_tokens:
        return 0.0, ""
    excerpt_str = " ".join(excerpt_tokens)
    n = len(excerpt_tokens)
    min_window = max(5, int(n * 0.8))
    max_window = int(n * 1.2) + 1
    best_ratio = 0.0
    best_window = ""
    for window_size in range(min_window, min(max_window + 1, len(opinion_tokens) + 1)):
        for i in range(len(opinion_tokens) - window_size + 1):
            window_tokens = opinion_tokens[i : i + window_size]
            window_str = " ".join(window_tokens)
            ratio = difflib.SequenceMatcher(None, excerpt_str, window_str).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_window = window_str
            if best_ratio >= 0.98:
                return best_ratio, best_window
    return best_ratio, best_window


def validate_excerpt(opinion_text, excerpt_text, opinion_length):
    """Run three-tier matching cascade on a single excerpt."""
    norm_opinion = normalize_text(opinion_text)
    norm_excerpt = normalize_text(strip_brackets(excerpt_text))
    if tier1_normalized_substring(norm_opinion, norm_excerpt):
        return {"status": "verified", "match_tier": "normalized_exact", "similarity": 1.0, "best_match_preview": ""}
    opinion_tokens = tokenize(opinion_text)
    if tier2_token_sequence(opinion_tokens, excerpt_text):
        return {"status": "verified", "match_tier": "token_sequence", "similarity": 1.0, "best_match_preview": ""}
    best_ratio, best_window = tier3_fuzzy_match(opinion_tokens, excerpt_text)
    if best_ratio >= 0.92:
        return {"status": "likely_match", "match_tier": "fuzzy", "similarity": round(best_ratio, 3), "best_match_preview": best_window[:200]}
    elif best_ratio >= 0.85:
        return {"status": "possible_match", "match_tier": "fuzzy", "similarity": round(best_ratio, 3), "best_match_preview": best_window[:200]}
    else:
        status = "not_found_truncated" if opinion_length >= 49500 else "not_found"
        return {"status": status, "match_tier": "none", "similarity": round(best_ratio, 3), "best_match_preview": best_window[:200]}


def main():
    if len(sys.argv) != 3:
        print(json.dumps({"error": "Usage: vq_matcher.py <opinion_file> <excerpts_file>"}))
        sys.exit(1)
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        opinion_text = f.read()
    with open(sys.argv[2], "r", encoding="utf-8") as f:
        excerpts = json.load(f)
    opinion_length = len(opinion_text)
    results = []
    for i, excerpt_obj in enumerate(excerpts):
        excerpt_text = excerpt_obj.get("text", "")
        if not excerpt_text.strip():
            results.append({"excerpt_index": i, "status": "skipped", "match_tier": "none", "similarity": 0.0, "best_match_preview": ""})
            continue
        result = validate_excerpt(opinion_text, excerpt_text, opinion_length)
        result["excerpt_index"] = i
        results.append(result)
    print(json.dumps(results))


if __name__ == "__main__":
    main()
