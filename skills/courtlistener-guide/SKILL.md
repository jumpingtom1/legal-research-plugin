---
name: courtlistener-guide
description: Best practices for legal research using CourtListener case law database, including search strategies, Bluebook citation format, and tool usage guidance. Use when discussing legal research, case law searches, CourtListener, or citation formats.
---

# CourtListener Legal Research Guide

## Available Tools

Five MCP tools provide access to the CourtListener case law database:

- **`mcp__plugin_legal_research_courtlistener__search_cases`**: Keyword search for opinions. Supports exact phrase matching with quotes, Boolean operators. Params: query, court, filed_after, filed_before, order_by, limit (max 20).
- **`mcp__plugin_legal_research_courtlistener__semantic_search`**: Natural language / conceptual search. Finds cases using similar concepts even with different terminology. Put specific terms in quotes to force exact matching within semantic results. Params: query, court, filed_after, filed_before, limit (max 20).
- **`mcp__plugin_legal_research_courtlistener__lookup_citation`**: Resolves legal citations (e.g., "410 U.S. 113") to their corresponding cases. Can extract multiple citations from surrounding text.
- **`mcp__plugin_legal_research_courtlistener__get_case_text`**: Retrieves the full text of a court opinion by cluster_id or opinion_id. Returns up to 50,000 characters; longer opinions are truncated with a link to the full version.
- **`mcp__plugin_legal_research_courtlistener__find_citing_cases`**: Finds cases that cite a given case (by cluster_id). Useful for tracing how a doctrine has developed or finding the most recent applications.

## Bluebook Citation Format

All case citations must follow standard Bluebook format:

- **Supreme Court**: _Case Name_, Vol. U.S. Pg. (Year) — e.g., _Roe v. Wade_, 410 U.S. 113 (1973)
- **Federal Circuit**: _Case Name_, Vol. F.3d/F.4th Pg. (Cir. Year) — e.g., _Smith v. Jones_, 500 F.3d 123 (9th Cir. 2020)
- **Federal District**: _Case Name_, Vol. F. Supp. 3d Pg. (D. State Year)
- **State Courts**: _Case Name_, Vol. Reporter Pg. (Court Year)
- Include parallel citations where available
- Case names are italicized: _Plaintiff v. Defendant_

Construct Bluebook citations from CourtListener data: combine volume, reporter, and page from the citations array with the court and date_filed fields.

## Court Codes

Common CourtListener court filter codes:
- **Federal**: scotus, ca1-ca11, cadc, cafc
- **State examples**: cal (California Supreme Court), calctapp (California Court of Appeal), ny, tex, fla
- Multiple courts can be space-separated: "ca9 ca10 scotus"

## Sort Options

- `"score desc"` — relevance (default, best for most searches)
- `"dateFiled desc"` — newest first (good for finding recent developments)
- `"dateFiled asc"` — oldest first (good for tracing doctrinal origins)
- `"citeCount desc"` — most cited (good for finding landmark cases)

## Search Strategy Best Practices

### Use Both Keyword and Semantic Search
Keyword search finds cases with exact terminology. Semantic search finds conceptually similar cases even when different words are used. Always use both for comprehensive coverage.

### Vary Terminology
Legal concepts often have multiple phrasings:
- "excessive force" / "unreasonable force" / "police brutality" / "use of deadly force"
- "qualified immunity" / "clearly established right" / "objective reasonableness"
- "due process" / "substantive due process" / "procedural due process"

### Include Doctrinal Terms
Legal doctrines have specific names. Include them:
- Case names that define doctrine (e.g., "Graham v. Connor", "Monell", "Chevron")
- Legal tests (e.g., "strict scrutiny", "rational basis", "Lemon test")
- Statutory references (e.g., "Section 1983", "42 U.S.C. 1983")

### Use find_citing_cases for Development
After finding a seminal case, use `find_citing_cases` to discover how the doctrine has been applied, extended, or limited in later decisions.

### Filter by Court and Date Strategically
- Search SCOTUS for foundational precedent regardless of jurisdiction
- Filter to the relevant circuit for binding authority
- Use date filters when there is a statutory change or landmark case that creates a natural boundary

## Boolean Query Syntax for Keyword Search

CourtListener's `search_cases` tool passes the query string directly to a **Solr-based search backend**. Understanding the syntax is critical to getting useful results.

### Syntax Reference

| Syntax | Meaning | Example |
|--------|---------|---------|
| `term1 term2` | Implicit AND — both terms must appear | `qualified immunity` → must contain both words (anywhere in text) |
| `"exact phrase"` | Phrase match — words must appear together in order | `"qualified immunity"` → exact phrase |
| `term1 OR term2` | Either term matches | `"excessive force" OR "unreasonable force"` |
| `term1 AND term2` | Both must appear (explicit AND, same as implicit) | `"qualified immunity" AND "traffic stop"` |
| `NOT term` or `-term` | Exclude a term | `"qualified immunity" NOT prison` |
| `(group)` | Grouping for complex Boolean logic | `("excessive force" OR "unreasonable force") AND "qualified immunity"` |
| `wild*` | Wildcard — matches any suffix | `immun*` → immunity, immunities, immunize |

### The AND-Narrowing Problem

The most common mistake is AND-joining too many terms. Every quoted phrase or term implicitly requires AND, so a case must contain **ALL** terms to match.

**Too narrow (common mistake):**
`"qualified immunity" "excessive force" "traffic stop" "Fourth Amendment" "clearly established"` — 5 quoted phrases implicitly AND-joined. A case must contain ALL FIVE exact phrases. Even a directly on-point case might use "vehicle stop" instead of "traffic stop" and be missed.

**Better approach — limit to 2-3 concepts per query:**
- `"qualified immunity" AND "excessive force"` — two concepts, focused
- `"qualified immunity" AND ("excessive force" OR "unreasonable force")` — two concepts, one with synonyms

### The OR-Broadening Problem

Too many OR-joined terms without an anchoring concept returns thousands of irrelevant results.

**Too broad:**
`police OR force OR immunity OR stop OR rights` — matches virtually any case mentioning any of these common words.

### Recommended Pattern

Structure queries as: `[Core Concept A] AND ([Variant B1] OR [Variant B2])`

**Good examples:**
- `"qualified immunity" AND ("excessive force" OR "unreasonable force" OR "police brutality")`
- `"Fourth Amendment" AND ("traffic stop" OR "vehicle stop" OR "roadside stop")`
- `"clearly established" AND ("use of force" OR "deadly force")`
- `"hostile work environment" AND "Title VII"`

### Tips

- **2-3 concepts per keyword query, not 5+** — run multiple focused queries instead of one mega-query
- **Always quote multi-word legal terms**: `"qualified immunity"` not `qualified immunity`
- **Use OR for synonyms of the same concept, AND between different concepts**
- **Reserve broad natural language for semantic search** (`semantic_search`), not keyword search
- **Include leading case names** as their own queries: `"Graham v. Connor" AND "traffic"`

## Public URLs

CourtListener opinion URLs follow the pattern:
`https://www.courtlistener.com/opinion/{cluster_id}/{slug}/`

These are returned in search results. Always include them in research output to give users easy access to the full opinions.

## Rate Limits

CourtListener allows 5,000 API requests per day. A typical research session uses 50-100 calls. Monitor usage if conducting extensive research.
