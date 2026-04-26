"""Materials enrichment — Tavily search + LLM extraction for supplier
catalog # / price.

Pipeline shape:

    Protocol → Materials → adapt_materials (FE shape) → enrich_materials_view

Why "after adapt_materials":
  - The view is already grouped + named the way the FE renders, so we
    walk it once and mutate items in place.
  - We don't need anything from the rich BE shape that the FE view
    doesn't already carry (name, qty, material_id).

Defensibility / reproducibility:
  - Tavily searches go through the existing `src/clients/tavily.py`
    cache (30-day TTL on supplier searches), so identical material
    names across runs hit the same upstream URLs.
  - The extractor is one LLM call per item. Output schema requires
    `source_url` — without a citation, the parser drops the entry
    and the FE falls back to "TBD" / null. Same defensibility
    pattern as Phase D (critique) and Phase E (key_differences).
  - Conservative: any extractor failure (no Tavily hits, LLM error,
    no source_url, ambiguous results) leaves the item's enrichment
    fields null. We never fabricate a supplier or a price.

Out of scope (for now):
  - Quantity-aware pricing (we surface whatever pack-size pricing
    Tavily lands on; downstream Stage 4 budget can re-search at
    the right size).
  - Currency conversion (return whatever currency the source page
    quotes; FE renders verbatim).
  - Supplier preference / fallback ranking (current behavior: take
    Tavily's top result whose domain is on the SUPPLIER_DOMAINS
    allowlist).
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional
from urllib.parse import urlparse

from src.clients import llm
from src.clients import tavily as tavily_client

from .frontend_view import FEMaterialsView, FEReagent


_LOG = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# LLM extractor
# --------------------------------------------------------------------------
# We prompt the LLM to read the Tavily search snippets and pick out
# {supplier, catalog, price, source_url}. The schema enforces source_url
# so every enriched field is auditable to a specific URL — fabrications
# without a source get dropped.

EXTRACT_SYSTEM = """You extract supplier procurement data from web search results.

You will receive a material name (e.g. "Glucose", "DMEM media", "1.5 mL microcentrifuge tubes") and 1-3 web search result snippets from supplier domains (Sigma-Aldrich, ThermoFisher, Promega, Qiagen, etc.).

Your job: pick the SINGLE result that best matches the material and extract:
- supplier        (e.g. "Sigma-Aldrich")
- catalog         (the supplier's catalog / SKU / part number, e.g. "G8270")
- price           (the listed price + pack size, e.g. "$45 / 500g". If missing, return null.)
- source_url      (the URL of the result you extracted from — REQUIRED)

Hard rules:
- Pick a result ONLY if it matches the material name. A "Glucose meter" page is NOT a match for "Glucose"; "Trypsin-EDTA" is NOT a match for "Trypsin".
- If NO result matches confidently, return all-null fields. Do not guess.
- Do not invent catalog numbers. If the snippet doesn't show the SKU, leave catalog null.
- Do not invent prices. If the snippet doesn't show a price, leave price null.
- source_url is REQUIRED whenever any other field is non-null. If you can't tie a value to a specific URL, return all-null.

Return ONLY a single valid JSON object:
{
  "supplier": "string | null",
  "catalog": "string | null",
  "price": "string | null",
  "source_url": "string | null",
  "match_confidence": "high" | "medium" | "low"
}"""


EXTRACT_USER_TMPL = """Material name: {name}
Material purpose: {purpose}

Search results ({n}):
{results_blob}"""


def _format_results(results: list[dict]) -> str:
    """Format Tavily results into a compact LLM-readable blob. We pass
    title + url + content snippet — enough for the LLM to recognize a
    matching product page and pluck the catalog / price."""
    lines: list[str] = []
    for i, r in enumerate(results[:3]):
        title = r.get("title") or "(no title)"
        url = r.get("url") or ""
        snippet = (r.get("content") or "")[:500]
        lines.append(
            f"[{i}] {title}\n"
            f"    URL: {url}\n"
            f"    {snippet}"
        )
    return "\n\n".join(lines) or "(no results)"


def _extract_one(
    name: str,
    purpose: str,
    results: list[dict],
) -> dict[str, Optional[str]]:
    """Single LLM call that picks the best-matching result and extracts
    the procurement fields. Returns a dict with `supplier`, `catalog`,
    `price`, `source_url` all `None` when no confident match was found
    (which the FE renders as the default 'TBD' state)."""
    null_result: dict[str, Optional[str]] = {
        "supplier": None,
        "catalog": None,
        "price": None,
        "source_url": None,
    }
    if not results:
        return null_result

    user = EXTRACT_USER_TMPL.format(
        name=name,
        purpose=purpose or "(not specified)",
        n=len(results),
        results_blob=_format_results(results),
    )

    try:
        parsed = llm.complete_json(
            EXTRACT_SYSTEM,
            user,
            agent_name="Materials enrichment",
        )
    except Exception as exc:
        _LOG.warning("Materials enrichment LLM call failed for %r: %s", name, exc)
        return null_result

    if not isinstance(parsed, dict):
        return null_result

    src = parsed.get("source_url")
    if not isinstance(src, str) or not src.strip():
        # No source URL → can't audit. Drop everything.
        return null_result

    # Validate the URL parses; otherwise the source claim is unverifiable.
    try:
        if not urlparse(src.strip()).netloc:
            return null_result
    except Exception:
        return null_result

    confidence = str(parsed.get("match_confidence") or "").lower()
    if confidence == "low":
        # The LLM itself flagged this as uncertain — better to render
        # "TBD" than mislead the researcher.
        return null_result

    def _clean(v: Any) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        if not s or s.lower() in {"null", "none", "n/a", "tbd", "-"}:
            return None
        # Cap length so a malformed extractor can't bloat the FE
        if len(s) > 200:
            s = s[:200] + "…"
        return s

    return {
        "supplier": _clean(parsed.get("supplier")),
        "catalog": _clean(parsed.get("catalog")),
        "price": _clean(parsed.get("price")),
        "source_url": src.strip(),
    }


# --------------------------------------------------------------------------
# Per-item enrichment
# --------------------------------------------------------------------------

def _query_for(item: FEReagent) -> str:
    """Build the Tavily query for a single item. Just the name on the
    supplier-domain-filtered search is usually enough; the existing
    `search_for_supplier` helper already appends 'catalog number' and
    scopes to the SUPPLIER_DOMAINS allowlist."""
    return (item.name or "").strip()


def enrich_one_item(item: FEReagent) -> FEReagent:
    """Fetch supplier/catalog/price for one material via Tavily +
    LLM extraction. Returns a new FEReagent with enrichment fields
    populated where extraction succeeded; original fields unchanged
    when extraction fails (FE keeps showing 'TBD'). Never mutates
    the input."""
    name = (item.name or "").strip()
    if not name or len(name) < 3:
        return item

    try:
        response = tavily_client.search_for_supplier(name)
    except Exception as exc:
        _LOG.warning("Tavily supplier search failed for %r: %s", name, exc)
        return item

    results = response.get("results") if isinstance(response, dict) else None
    if not isinstance(results, list) or not results:
        return item

    extracted = _extract_one(name, item.purpose or "", results)

    # Apply only when we got a non-null source URL; otherwise leave the
    # item's existing values (the model_copy below is a no-op when
    # everything is None).
    if not extracted["source_url"]:
        return item

    # Don't clobber an existing supplier/catalog with None — the
    # original adapt_materials may have populated those from the BE.
    updates: dict[str, Any] = {"source_url": extracted["source_url"]}
    if extracted["supplier"]:
        updates["supplier"] = extracted["supplier"]
    if extracted["catalog"]:
        updates["catalog"] = extracted["catalog"]
    if extracted["price"]:
        updates["price"] = extracted["price"]

    return item.model_copy(update=updates)


# --------------------------------------------------------------------------
# Top-level: walk the FE view
# --------------------------------------------------------------------------

def enrich_materials_view(
    view: FEMaterialsView,
    *,
    max_workers: int = 6,
) -> FEMaterialsView:
    """Walk every item across every group, enrich in parallel, and
    return a new view with the enrichment fields populated. Never
    raises — best-effort across the whole list. Cached upstream
    (30-day TTL on supplier searches) so reruns of the same plan
    are essentially free."""
    # Flatten with backreferences so we can reassemble in original order.
    flat: list[tuple[int, int, FEReagent]] = []
    for gi, group in enumerate(view.groups):
        for ii, item in enumerate(group.items):
            flat.append((gi, ii, item))

    if not flat:
        return view

    n_workers = min(len(flat), max_workers)
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        enriched = list(pool.map(lambda t: enrich_one_item(t[2]), flat))

    # Reassemble into a new view; copy groups + items so we never
    # mutate the input.
    new_groups = []
    for gi, group in enumerate(view.groups):
        new_items = list(group.items)
        for (gi2, ii, _orig), new_item in zip(flat, enriched):
            if gi2 == gi:
                new_items[ii] = new_item
        new_groups.append(group.model_copy(update={"items": new_items}))

    return view.model_copy(update={"groups": new_groups})
