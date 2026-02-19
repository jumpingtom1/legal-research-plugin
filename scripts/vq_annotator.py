#!/usr/bin/env python3
"""
Annotate research HTML with quote validation results.
Input: HTML file path, validation results JSON file path
Output: Annotated HTML written to the same file
"""

import sys
import json
import re
import html


def normalize_for_comparison(text):
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def find_best_match(blockquote_text, results):
    norm_bq = normalize_for_comparison(blockquote_text).lower()
    best_match = None
    best_overlap = 0
    for r in results:
        preview = r.get("excerpt_text", "").lower()
        if not preview:
            continue
        norm_preview = re.sub(r"\s+", " ", preview).strip().lower()
        if norm_preview in norm_bq or norm_bq in norm_preview:
            overlap = len(norm_preview)
            if overlap > best_overlap:
                best_overlap = overlap
                best_match = r
            continue
        short_preview = norm_preview[:60]
        if short_preview and short_preview in norm_bq:
            overlap = len(short_preview)
            if overlap > best_overlap:
                best_overlap = overlap
                best_match = r
    return best_match


STATUS_CONFIG = {
    "verified": {"css_class": "quote-verified", "label": "[Verified]", "color": "#27ae60"},
    "likely_match": {"css_class": "quote-likely", "label": "[Likely Match — {pct}%]", "color": "#6b8e23"},
    "possible_match": {"css_class": "quote-possible", "label": "[Unverified — {pct}%]", "color": "#e67e22"},
    "not_found_truncated": {"css_class": "quote-truncated", "label": "[Unverified — opinion truncated]", "color": "#888"},
    "not_found": {"css_class": "quote-not-found", "label": "[UNVERIFIED — {pct}%]", "color": "#e74c3c"},
}


def build_annotation(result):
    status = result["status"]
    config = STATUS_CONFIG.get(status)
    if not config:
        return ""
    pct = int(result.get("similarity", 0) * 100)
    label = config["label"].format(pct=pct)
    annotation = f'<span style="color: {config["color"]}; font-size: 0.8em; font-style: normal;'
    if status in ("not_found", "possible_match"):
        annotation += " font-weight: bold;"
    annotation += f'">{label}</span>'
    if status == "not_found" and result.get("best_match_preview"):
        preview = html.escape(result["best_match_preview"][:200])
        annotation += (
            f'\n<details style="margin-top: 0.3em; font-size: 0.85em;">'
            f'<summary style="color: #e74c3c; cursor: pointer;">Show closest match in opinion</summary>'
            f'<p style="color: #666; font-style: normal; padding: 0.5em; background: #f9f9f9; '
            f'border-radius: 4px;">{preview}...</p></details>'
        )
    return annotation


def annotate_html(html_content, results):
    blockquote_pattern = re.compile(r"(<blockquote(?:\s[^>]*)?>)(.*?)(</blockquote>)", re.DOTALL)
    used_results = set()

    def replace_blockquote(match):
        open_tag = match.group(1)
        content = match.group(2)
        close_tag = match.group(3)
        best = find_best_match(content, [r for i, r in enumerate(results) if i not in used_results])
        if best is None:
            return match.group(0)
        idx = next(i for i, r in enumerate(results) if r is best)
        used_results.add(idx)
        config = STATUS_CONFIG.get(best["status"], {})
        css_class = config.get("css_class", "")
        if "class=" in open_tag:
            new_open = re.sub(r'class="([^"]*)"', f'class="\\1 {css_class}"', open_tag)
        else:
            new_open = open_tag.replace("<blockquote", f'<blockquote class="{css_class}"', 1)
        annotation = build_annotation(best)
        return f"{new_open}{content}{annotation}{close_tag}"

    return blockquote_pattern.sub(replace_blockquote, html_content)


def main():
    if len(sys.argv) != 3:
        print("Usage: vq_annotator.py <html_file> <results_json_file>", file=sys.stderr)
        sys.exit(1)
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        html_content = f.read()
    with open(sys.argv[2], "r", encoding="utf-8") as f:
        results = json.load(f)
    annotated = annotate_html(html_content, results)
    with open(sys.argv[1], "w", encoding="utf-8") as f:
        f.write(annotated)
    print(f"Annotated {len(results)} excerpts in {sys.argv[1]}", file=sys.stderr)


if __name__ == "__main__":
    main()
