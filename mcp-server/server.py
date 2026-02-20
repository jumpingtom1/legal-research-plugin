"""CourtListener MCP Server — search case law by citation, keyword, or semantic text."""

import os
import re
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
from mcp.server.fastmcp import FastMCP, Context

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_TOKEN = os.environ.get("COURTLISTENER_API_TOKEN", "")
BASE_V3 = "https://www.courtlistener.com/api/rest/v3"
BASE_V4 = "https://www.courtlistener.com/api/rest/v4"

# ---------------------------------------------------------------------------
# Lifespan — shared httpx client
# ---------------------------------------------------------------------------


@dataclass
class AppContext:
    client: httpx.AsyncClient


@asynccontextmanager
async def app_lifespan(app: FastMCP) -> AsyncIterator[AppContext]:
    headers = {"Authorization": f"Token {API_TOKEN}"} if API_TOKEN else {}
    async with httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(30.0),
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
    ) as client:
        yield AppContext(client=client)


mcp = FastMCP(
    "courtlistener",
    instructions=(
        "Legal research server providing access to the CourtListener case law database "
        "and the PACER/RECAP federal court docket archive. "
        "Case law tools: search_cases (keyword search), semantic_search (natural language), "
        "lookup_citation (resolve citations), get_case_text (full opinion text), "
        "find_citing_cases (citation network). "
        "PACER/docket tools: search_dockets (search RECAP archive), get_docket (docket metadata), "
        "get_docket_entries (filings list), get_parties (parties and attorneys), "
        "get_recap_document (document metadata and download URL). "
        "Typical docket workflow: search_dockets -> get_docket -> get_docket_entries -> get_parties."
    ),
    lifespan=app_lifespan,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_client(ctx: Context) -> httpx.AsyncClient:
    return ctx.request_context.lifespan_context.client


async def api_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs,
) -> dict | list | str:
    """Make an API request with standardised error handling.

    Returns parsed JSON (dict or list) on success, or an error string.
    """
    if not API_TOKEN:
        return "Error: COURTLISTENER_API_TOKEN environment variable is not set."
    try:
        response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code == 401:
            return "Error: Invalid API token. Check COURTLISTENER_API_TOKEN."
        if code == 429:
            return "Error: Rate limit exceeded. CourtListener allows 5,000 requests/day."
        if code == 404:
            return "Error: Resource not found."
        return f"Error: HTTP {code} — {exc.response.text[:300]}"
    except httpx.TimeoutException:
        return "Error: Request timed out after 30 seconds."
    except httpx.RequestError as exc:
        return f"Error: Connection failed — {exc}"


def format_search_results(data: dict, header: str) -> str:
    """Format a v4 search response into readable text."""
    count = data.get("count", 0)
    results = data.get("results", [])

    if not results:
        return f"No results found. {header}"

    lines: list[str] = [f"{header} ({count} total results)\n"]

    for i, case in enumerate(results, 1):
        cites = case.get("citations") or []
        if isinstance(cites, list):
            if cites and isinstance(cites[0], dict):
                cite_strs = [
                    f"{c.get('volume', '')} {c.get('reporter', '')} {c.get('page', '')}".strip()
                    for c in cites
                ]
            else:
                cite_strs = [str(c) for c in cites]
        else:
            cite_strs = [str(cites)]
        citation_text = ", ".join(cite_strs) or "None"

        snippet = case.get("snippet", "N/A")
        # Strip HTML highlight tags from snippet
        snippet = re.sub(r"</?mark>", "", snippet)

        lines.append(
            f"{i}. {case.get('caseName', 'Unknown')}"
            f" ({case.get('court', '?')}, {case.get('dateFiled', '?')})\n"
            f"   Citations: {citation_text}\n"
            f"   Cited by: {case.get('citeCount', case.get('citation_count', 0))} cases\n"
            f"   Snippet: {snippet}\n"
            f"   Cluster ID: {case.get('cluster_id', 'N/A')}\n"
            f"   URL: https://www.courtlistener.com{case.get('absolute_url', '')}\n"
        )

    return "\n".join(lines)


def _ok(response: str) -> str:
    """Wrap a successful API response with a machine-readable status prefix.

    Agents check for this prefix to verify the API returned a 200 response.
    Absence of this prefix means the call failed (error string was returned instead).
    """
    return f"API_STATUS:200\n{response}"


def _search_params(
    query: str,
    court: str,
    filed_after: str,
    filed_before: str,
    order_by: str,
    limit: int,
) -> dict:
    """Build query params for a v4 opinion search."""
    params: dict = {"type": "o", "q": query, "order_by": order_by}
    if court:
        params["court"] = court
    if filed_after:
        params["filed_after"] = filed_after
    if filed_before:
        params["filed_before"] = filed_before
    params["limit"] = min(max(limit, 1), 20)
    return params


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def search_cases(
    query: str,
    court: str = "",
    filed_after: str = "",
    filed_before: str = "",
    order_by: str = "score desc",
    limit: int = 10,
    ctx: Context = None,
) -> str:
    """Search CourtListener for case law opinions by keywords.

    Args:
        query: Keywords to search for (e.g. "fourth amendment search seizure").
               Use quotes around phrases for exact matching.
        court: Court filter code (e.g. "scotus", "ca9", "or orctapp").
               Multiple courts separated by spaces.
        filed_after: Start date filter in YYYY-MM-DD format.
        filed_before: End date filter in YYYY-MM-DD format.
        order_by: Sort order. Options: "score desc" (relevance), "dateFiled desc"
                  (newest), "dateFiled asc" (oldest), "citeCount desc" (most cited).
        limit: Max results to return (1–20, default 10).
    """
    client = _get_client(ctx)
    params = _search_params(query, court, filed_after, filed_before, order_by, limit)
    data = await api_request(client, "GET", f"{BASE_V4}/search/", params=params)
    if isinstance(data, str):
        return data
    return _ok(format_search_results(data, f'Search results for "{query}"'))


@mcp.tool()
async def lookup_citation(
    citation: str,
    ctx: Context = None,
) -> str:
    """Look up a legal citation and resolve it to the corresponding case.

    Args:
        citation: A legal citation string (e.g. "410 U.S. 113", "576 US 644",
                  "347 U.S. 483"). Can also include surrounding text — the API
                  will extract and resolve all citations found.
    """
    client = _get_client(ctx)
    data = await api_request(
        client, "POST", f"{BASE_V3}/citation-lookup/", data={"text": citation}
    )
    if isinstance(data, str):
        return data

    if not data:
        return f"No cases found for citation: {citation}"

    lines: list[str] = []
    for item in data:
        cite_str = item.get("citation", "?")
        normalized = item.get("normalized_citations", [])
        status = item.get("status", "?")

        lines.append(f"Citation: {cite_str}")
        if normalized:
            lines.append(f"Normalized: {', '.join(normalized)}")
        lines.append(f"Status: {status}")

        clusters = item.get("clusters")
        if clusters and status == 200:
            # clusters may be a single object or list depending on version
            if isinstance(clusters, dict):
                clusters = [clusters]
            for cl in clusters:
                case_name = cl.get("case_name", "Unknown")
                date_filed = cl.get("date_filed", "?")
                cl_id = cl.get("id", "?")
                url = cl.get("absolute_url", "")
                cites = cl.get("citations", [])
                cite_text = ", ".join(
                    f"{c.get('volume', '')} {c.get('reporter', '')} {c.get('page', '')}".strip()
                    for c in cites
                ) if cites else "None"

                lines.append(f"\nCase: {case_name}")
                lines.append(f"Date Filed: {date_filed}")
                lines.append(f"Citations: {cite_text}")
                lines.append(f"Cluster ID: {cl_id}")
                if url:
                    lines.append(f"URL: https://www.courtlistener.com{url}")
        elif status == 404:
            lines.append("No matching case found for this citation.")

        lines.append("")

    return "\n".join(lines).strip()


@mcp.tool()
async def semantic_search(
    query: str,
    court: str = "",
    filed_after: str = "",
    filed_before: str = "",
    limit: int = 10,
    ctx: Context = None,
) -> str:
    """Search for case law using natural language / semantic similarity.

    Unlike keyword search, this finds conceptually similar cases even when
    different terminology is used. Put specific required terms in quotation
    marks to force exact keyword matching within semantic results.

    Args:
        query: Natural language description of the legal concept
               (e.g. "when can police search a car without a warrant").
        court: Court filter code (e.g. "scotus", "ca9").
        filed_after: Start date filter in YYYY-MM-DD format.
        filed_before: End date filter in YYYY-MM-DD format.
        limit: Max results to return (1–20, default 10).
    """
    client = _get_client(ctx)
    params = _search_params(query, court, filed_after, filed_before, "score desc", limit)
    params["semantic"] = "true"
    data = await api_request(client, "GET", f"{BASE_V4}/search/", params=params)
    if isinstance(data, str):
        return data
    return _ok(format_search_results(data, f'Semantic search results for "{query}"'))


@mcp.tool()
async def get_case_text(
    cluster_id: int = 0,
    opinion_id: int = 0,
    max_characters: int = 50000,
    ctx: Context = None,
) -> str:
    """Retrieve the full text of a court opinion.

    Provide either a cluster_id (case-level ID from search results) or a
    specific opinion_id. If cluster_id is provided, fetches the primary
    opinion in the cluster.

    Args:
        cluster_id: The cluster ID of the case (from search results).
        opinion_id: The specific opinion ID (if known).
        max_characters: Maximum characters of opinion text to return (default 50000).
    """
    if cluster_id == 0 and opinion_id == 0:
        return "Error: Provide either cluster_id or opinion_id."

    client = _get_client(ctx)

    # Resolve cluster_id → opinion_id if needed
    case_name = "Unknown"
    date_filed = "?"
    court = "?"
    case_url = ""

    if opinion_id == 0:
        cluster_data = await api_request(
            client,
            "GET",
            f"{BASE_V3}/clusters/{cluster_id}/",
        )
        if isinstance(cluster_data, str):
            return cluster_data

        case_name = cluster_data.get("case_name", "Unknown")
        date_filed = cluster_data.get("date_filed", "?")
        case_url = cluster_data.get("absolute_url", "")

        sub_opinions = cluster_data.get("sub_opinions", [])
        if not sub_opinions:
            return f"Error: No opinions found in cluster {cluster_id}."

        # Extract opinion ID from URL: ".../opinions/12345/"
        first_url = sub_opinions[0] if isinstance(sub_opinions[0], str) else ""
        match = re.search(r"/opinions/(\d+)/", first_url)
        if match:
            opinion_id = int(match.group(1))
        else:
            return f"Error: Could not parse opinion ID from cluster data."

    # Fetch the opinion
    opinion_data = await api_request(
        client,
        "GET",
        f"{BASE_V3}/opinions/{opinion_id}/",
    )
    if isinstance(opinion_data, str):
        return opinion_data

    # Extract text (priority: plain_text > html variants)
    text = opinion_data.get("plain_text", "")
    source = "plain_text"

    if not text:
        for field in [
            "html_with_citations",
            "html",
            "html_columbia",
            "html_lawbox",
            "html_anon_2020",
            "xml_harvard",
        ]:
            raw = opinion_data.get(field, "")
            if raw:
                text = re.sub(r"<[^>]+>", "", raw)
                source = field
                break

    if not text:
        return f"Error: No text content available for opinion {opinion_id}."

    # Get metadata from opinion if we didn't fetch cluster
    author = opinion_data.get("author_str", "")
    op_type = opinion_data.get("type", "")

    # Truncate if needed
    truncated = False
    if len(text) > max_characters:
        text = text[:max_characters]
        truncated = True

    lines = []
    if case_name != "Unknown":
        lines.append(f"Case: {case_name}")
    if date_filed != "?":
        lines.append(f"Date: {date_filed}")
    if author:
        lines.append(f"Author: {author}")
    if op_type:
        lines.append(f"Opinion Type: {op_type}")
    lines.append(f"Text Source: {source}")
    lines.append(f"Opinion ID: {opinion_id}")
    lines.append("")
    lines.append("--- OPINION TEXT ---")
    lines.append(text)

    if truncated:
        full_url = f"https://www.courtlistener.com{case_url}" if case_url else ""
        lines.append(
            f"\n[Truncated at {max_characters:,} characters. "
            f"Full opinion: {full_url}]"
        )

    return _ok("\n".join(lines))


@mcp.tool()
async def find_citing_cases(
    cluster_id: int,
    court: str = "",
    filed_after: str = "",
    filed_before: str = "",
    order_by: str = "score desc",
    limit: int = 10,
    ctx: Context = None,
) -> str:
    """Find cases that cite a given case.

    Args:
        cluster_id: Cluster ID of the case to find citations for
                    (obtain from search results or lookup_citation).
        court: Court filter code (e.g. "scotus", "ca9").
        filed_after: Start date filter in YYYY-MM-DD format.
        filed_before: End date filter in YYYY-MM-DD format.
        order_by: Sort order (default: "score desc").
        limit: Max results to return (1–20, default 10).
    """
    client = _get_client(ctx)
    query = f"cites:({cluster_id})"
    params = _search_params(query, court, filed_after, filed_before, order_by, limit)
    data = await api_request(client, "GET", f"{BASE_V4}/search/", params=params)
    if isinstance(data, str):
        return data
    return _ok(format_search_results(data, f"Cases citing cluster {cluster_id}"))


# ---------------------------------------------------------------------------
# PACER / RECAP helpers
# ---------------------------------------------------------------------------


def _docket_search_params(
    query: str,
    court: str,
    filed_after: str,
    filed_before: str,
    order_by: str,
    limit: int,
) -> dict:
    """Build query params for a v4 RECAP docket search."""
    params: dict = {"type": "r", "q": query, "order_by": order_by}
    if court:
        params["court"] = court
    if filed_after:
        params["filed_after"] = filed_after
    if filed_before:
        params["filed_before"] = filed_before
    params["limit"] = min(max(limit, 1), 20)
    return params


def format_docket_search_results(data: dict, header: str) -> str:
    """Format a v4 RECAP search response into readable text."""
    count = data.get("count", 0)
    results = data.get("results", [])

    if not results:
        return f"No results found. {header}"

    lines: list[str] = [f"{header} ({count} total results)\n"]

    for i, item in enumerate(results, 1):
        case_name = item.get("caseName", "Unknown")
        docket_number = item.get("docketNumber", "N/A")
        court_name = item.get("court", "?")
        date_filed = item.get("dateFiled", "?")
        date_terminated = item.get("dateTerminated", "")
        docket_id = item.get("docket_id", "N/A")
        assigned_to = item.get("assignedTo", "")
        cause = item.get("cause", "")
        nature_of_suit = item.get("suitNature", "")
        absolute_url = item.get("absolute_url", "")

        entry = (
            f"{i}. {case_name}\n"
            f"   Docket Number: {docket_number}\n"
            f"   Court: {court_name}\n"
            f"   Date Filed: {date_filed}\n"
        )
        if date_terminated:
            entry += f"   Date Terminated: {date_terminated}\n"
        if assigned_to:
            entry += f"   Assigned To: {assigned_to}\n"
        if cause:
            entry += f"   Cause: {cause}\n"
        if nature_of_suit:
            entry += f"   Nature of Suit: {nature_of_suit}\n"
        entry += (
            f"   Docket ID: {docket_id}\n"
            f"   URL: https://www.courtlistener.com{absolute_url}\n"
        )
        lines.append(entry)

    return "\n".join(lines)


def format_docket(data: dict) -> str:
    """Format a single docket response into readable text."""
    lines: list[str] = []
    lines.append(f"Case: {data.get('case_name', 'Unknown')}")

    docket_number = data.get("docket_number", "")
    if docket_number:
        lines.append(f"Docket Number: {docket_number}")

    court_name = data.get("court_id", "?")
    lines.append(f"Court: {court_name}")

    for label, key in [
        ("Date Filed", "date_filed"),
        ("Date Terminated", "date_terminated"),
        ("Date Last Filing", "date_last_filing"),
        ("Assigned To", "assigned_to_str"),
        ("Referred To", "referred_to_str"),
        ("Cause", "cause"),
        ("Nature of Suit", "nature_of_suit"),
        ("Jury Demand", "jury_demand"),
        ("Jurisdiction Type", "jurisdiction_type"),
    ]:
        val = data.get(key, "")
        if val:
            lines.append(f"{label}: {val}")

    docket_id = data.get("id", "N/A")
    lines.append(f"Docket ID: {docket_id}")

    pacer_case_id = data.get("pacer_case_id", "")
    if pacer_case_id:
        lines.append(f"PACER Case ID: {pacer_case_id}")

    absolute_url = data.get("absolute_url", "")
    if absolute_url:
        lines.append(f"URL: https://www.courtlistener.com{absolute_url}")

    return "\n".join(lines)


def format_docket_entries(data: dict | list, docket_id: int) -> str:
    """Format docket entries list into readable text."""
    if isinstance(data, dict):
        count = data.get("count", 0)
        results = data.get("results", [])
    else:
        count = len(data)
        results = data

    if not results:
        return f"No docket entries found for docket {docket_id}."

    lines: list[str] = [f"Docket entries for docket {docket_id} ({count} total)\n"]

    for entry in results:
        entry_number = entry.get("entry_number", "?")
        date_filed = entry.get("date_filed", "?")
        description = entry.get("description", "")
        if description:
            description = re.sub(r"<[^>]+>", "", description)

        entry_line = f"  #{entry_number} ({date_filed})"
        if description:
            entry_line += f": {description}"

        recap_docs = entry.get("recap_documents", [])
        if recap_docs:
            doc_parts = []
            for doc in recap_docs:
                doc_id = doc.get("id", "?")
                doc_desc = doc.get("description", "") or doc.get("short_description", "")
                doc_type = doc.get("document_type_name", "")
                page_count = doc.get("page_count")
                part = f"    - Doc {doc_id}"
                if doc_type:
                    part += f" [{doc_type}]"
                if doc_desc:
                    part += f": {re.sub(r'<[^>]+>', '', doc_desc)}"
                if page_count:
                    part += f" ({page_count} pages)"
                doc_parts.append(part)
            entry_line += "\n" + "\n".join(doc_parts)

        lines.append(entry_line)

    return "\n".join(lines)


def format_parties(data: dict | list, docket_id: int) -> str:
    """Format parties with nested attorneys into readable text."""
    if isinstance(data, dict):
        count = data.get("count", 0)
        results = data.get("results", [])
    else:
        count = len(data)
        results = data

    if not results:
        return f"No parties found for docket {docket_id}."

    lines: list[str] = [f"Parties for docket {docket_id} ({count} total)\n"]

    for party in results:
        name = party.get("name", "Unknown")
        party_type = party.get("party_type", "")
        extra_info = party.get("extra_info", "")

        header = f"  {name}"
        if party_type:
            header += f" ({party_type})"
        lines.append(header)

        if extra_info:
            lines.append(f"    Info: {extra_info}")

        attorneys = party.get("attorneys", [])
        for atty in attorneys:
            atty_name = atty.get("name", "Unknown")
            roles = atty.get("roles", [])
            role_strs = []
            for r in roles:
                if isinstance(r, dict):
                    role_strs.append(r.get("role_name", str(r)))
                else:
                    role_strs.append(str(r))
            role_text = ", ".join(role_strs) if role_strs else ""

            contact = atty.get("contact_raw", "")
            atty_line = f"    Attorney: {atty_name}"
            if role_text:
                atty_line += f" [{role_text}]"
            lines.append(atty_line)
            if contact:
                # Indent contact info
                for cline in contact.strip().split("\n"):
                    lines.append(f"      {cline.strip()}")

        lines.append("")  # blank line between parties

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# PACER / RECAP tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def search_dockets(
    query: str,
    court: str = "",
    filed_after: str = "",
    filed_before: str = "",
    order_by: str = "score desc",
    limit: int = 10,
    ctx: Context = None,
) -> str:
    """Search the RECAP archive for federal court dockets.

    RECAP contains millions of dockets and filings from PACER (the federal
    court electronic filing system). Use this to find federal cases by party
    name, case topic, docket number, etc.

    Args:
        query: Keywords to search for (e.g. "Apple v. Samsung", "bankruptcy",
               a docket number like "1:21-cv-00123").
        court: Court filter code (e.g. "nysd", "cacd", "txed").
               Multiple courts separated by spaces.
        filed_after: Start date filter in YYYY-MM-DD format.
        filed_before: End date filter in YYYY-MM-DD format.
        order_by: Sort order. Options: "score desc" (relevance), "dateFiled desc"
                  (newest), "dateFiled asc" (oldest).
        limit: Max results to return (1–20, default 10).
    """
    client = _get_client(ctx)
    params = _docket_search_params(query, court, filed_after, filed_before, order_by, limit)
    data = await api_request(client, "GET", f"{BASE_V4}/search/", params=params)
    if isinstance(data, str):
        return data
    return format_docket_search_results(data, f'RECAP docket search for "{query}"')


@mcp.tool()
async def get_docket(
    docket_id: int,
    ctx: Context = None,
) -> str:
    """Look up a specific federal court docket by its CourtListener docket ID.

    Returns metadata including case name, docket number, court, dates, assigned
    judge, cause, nature of suit, and jurisdiction type.

    Args:
        docket_id: The CourtListener docket ID (from search_dockets results).
    """
    client = _get_client(ctx)
    params = {
        "fields": (
            "id,absolute_url,case_name,docket_number,court_id,"
            "date_filed,date_terminated,date_last_filing,"
            "assigned_to_str,referred_to_str,cause,nature_of_suit,"
            "jury_demand,jurisdiction_type,pacer_case_id"
        ),
    }
    data = await api_request(
        client, "GET", f"{BASE_V4}/dockets/{docket_id}/", params=params
    )
    if isinstance(data, str):
        return data
    return format_docket(data)


@mcp.tool()
async def get_docket_entries(
    docket_id: int,
    entry_after: str = "",
    entry_before: str = "",
    order_by: str = "recap_sequence_number,entry_number",
    limit: int = 20,
    ctx: Context = None,
) -> str:
    """Get docket entries (filings) for a specific federal court docket.

    Note: This endpoint may be restricted to select users. If access is denied
    the API error message will be returned.

    Args:
        docket_id: The CourtListener docket ID.
        entry_after: Only entries filed after this date (YYYY-MM-DD).
        entry_before: Only entries filed before this date (YYYY-MM-DD).
        order_by: Sort order (default matches website order).
        limit: Max entries to return (1–20, default 20).
    """
    client = _get_client(ctx)
    params: dict = {
        "docket": docket_id,
        "omit": "recap_documents__plain_text",
        "order_by": order_by,
        "limit": min(max(limit, 1), 20),
    }
    if entry_after:
        params["date_filed__gte"] = entry_after
    if entry_before:
        params["date_filed__lte"] = entry_before

    data = await api_request(
        client, "GET", f"{BASE_V4}/docket-entries/", params=params
    )
    if isinstance(data, str):
        return data
    return format_docket_entries(data, docket_id)


@mcp.tool()
async def get_parties(
    docket_id: int,
    ctx: Context = None,
) -> str:
    """Get parties and their attorneys for a specific federal court docket.

    Note: This endpoint may be restricted to select users. If access is denied
    the API error message will be returned.

    Args:
        docket_id: The CourtListener docket ID.
    """
    client = _get_client(ctx)
    params = {
        "docket": docket_id,
        "filter_nested_results": "True",
    }
    data = await api_request(
        client, "GET", f"{BASE_V4}/parties/", params=params
    )
    if isinstance(data, str):
        return data
    return format_parties(data, docket_id)


@mcp.tool()
async def get_recap_document(
    recap_document_id: int,
    include_text: bool = False,
    ctx: Context = None,
) -> str:
    """Look up a specific RECAP document by its ID to get metadata and download URL.

    Note: This endpoint may be restricted to select users. If access is denied
    the API error message will be returned.

    Args:
        recap_document_id: The RECAP document ID (from docket entry results).
        include_text: If True, include the full plain text of the document.
                      Defaults to False for performance.
    """
    client = _get_client(ctx)
    params: dict = {}
    if not include_text:
        params["omit"] = "plain_text"
    data = await api_request(
        client, "GET", f"{BASE_V4}/recap-documents/{recap_document_id}/", params=params
    )
    if isinstance(data, str):
        return data

    lines: list[str] = []
    lines.append(f"RECAP Document {data.get('id', recap_document_id)}")

    description = data.get("description", "") or data.get("short_description", "")
    if description:
        lines.append(f"Description: {re.sub(r'<[^>]+>', '', description)}")

    doc_type = data.get("document_type_name", "")
    if doc_type:
        lines.append(f"Type: {doc_type}")

    doc_number = data.get("document_number", "")
    if doc_number:
        lines.append(f"Document Number: {doc_number}")

    attachment_number = data.get("attachment_number")
    if attachment_number:
        lines.append(f"Attachment Number: {attachment_number}")

    page_count = data.get("page_count")
    if page_count:
        lines.append(f"Pages: {page_count}")

    filepath = data.get("filepath_local", "")
    if filepath:
        lines.append(f"Download: https://storage.courtlistener.com/{filepath}")

    ia_url = data.get("filepath_ia", "")
    if ia_url:
        lines.append(f"Internet Archive: {ia_url}")

    if include_text:
        plain_text = data.get("plain_text", "")
        if plain_text:
            lines.append("")
            lines.append("--- DOCUMENT TEXT ---")
            lines.append(plain_text[:50000])
            if len(plain_text) > 50000:
                lines.append("\n[Truncated at 50,000 characters]")
        else:
            lines.append("\nNo plain text available for this document.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
