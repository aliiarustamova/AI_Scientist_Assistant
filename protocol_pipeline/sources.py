"""protocols.io sample loader + DraftJS normalizer.

Reads the static bundles in `pipeline_output_samples/protocols_io/<name>.json`
(produced by `tools/protocols_io_smoke.py`) and turns them into a clean
`NormalizedProtocol` shape the LLM agents can consume without seeing
DraftJS escape sequences, mojibake, or HTML wrappers.

Why a separate normalizer:
  - protocols.io stores step bodies as DraftJS blocks JSON inside a string
    field, so raw payloads are noisy. Feeding them to an LLM wastes tokens
    and primes the model to mimic the noise.
  - Some protocols are non-English (the trehalose top hit is Spanish).
    A single language flag lets prompts opt into "translate inline" rather
    than relying on the LLM to silently figure it out.
  - Section headers come back as `<p>...</p>`. Stripping them centrally
    means downstream code never has to think about the wrapper.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field


SAMPLES_DIR = Path("pipeline_output_samples/protocols_io")


# ---------------------------------------------------------------------------
# Normalized shape — what the LLM agents actually consume
# ---------------------------------------------------------------------------

class NormalizedStep(BaseModel):
    id: str                         # protocols.io step id (string, for stable refs)
    section: str                    # cleaned section header, e.g. "Methods"
    number: str                     # display step number from the protocol
    text: str                       # plaintext from DraftJS blocks
    duration_seconds: Optional[int] = None  # raw seconds from API


class NormalizedProtocol(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    authors: list[str] = Field(default_factory=list)
    language: str = "unknown"       # 'en' | 'es' | 'unknown'
    materials_text: str = ""        # plaintext extracted from materials_text DraftJS
    steps: list[NormalizedStep] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# DraftJS parsing
# ---------------------------------------------------------------------------

# Markers we prepend to list-type DraftJS blocks so the LLM still sees the
# structure (numbered vs bulleted) without us shipping raw block JSON.
_LIST_MARKERS = {
    "unordered-list-item": "- ",
    "ordered-list-item": "1. ",      # plain "1." rather than tracking ordinal
}

_TAG_RE = re.compile(r"<[^>]+>")

# When ``json.loads`` fails (huge/awkward payload), pull block ``"text"`` values.
_DRAFT_TEXT_FIELD_RE = re.compile(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"')
# Truncated / invalid JSON: string may be missing the closing ``"`.
_DRAFT_TEXT_FIELD_LOOSE_RE = re.compile(r'"text"\s*:\s*"((?:\\.|[^"])*)', re.DOTALL)


def _strip_html(text: str) -> str:
    """protocols.io section fields ship as `<p>Methods</p>`. Strip the tags."""
    if not text:
        return ""
    return _TAG_RE.sub("", text).strip()


def _unescape_draftjs_json_string(s: str) -> str:
    return (
        s.replace("\\n", " ")
        .replace("\\r", " ")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def _draftjs_loose_text_from_string(s: str) -> str:
    """Best-effort plaintext when full JSON parse is not available."""
    parts = _DRAFT_TEXT_FIELD_RE.findall(s)
    if not parts:
        parts = _DRAFT_TEXT_FIELD_LOOSE_RE.findall(s)
    if not parts:
        return ""
    return re.sub(
        r"\s+",
        " ",
        " ".join(_unescape_draftjs_json_string(p) for p in parts),
    ).strip()


def _get_blocks_list(obj: dict) -> Optional[list]:
    """Return the Draft.js ``blocks`` array (case-insensitive key)."""
    for k, v in obj.items():
        if isinstance(k, str) and k.lower() == "blocks" and isinstance(v, list):
            return v
    for v in obj.values():
        if not isinstance(v, dict):
            continue
        for ik, inner in v.items():
            if isinstance(ik, str) and ik.lower() == "blocks" and isinstance(inner, list):
                return inner
    return None


def _blocks_to_lines(blocks: list) -> str:
    lines: list[str] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        line = b.get("text")
        if line is None:
            continue
        text = str(line).strip()
        if not text:
            continue
        block_type = b.get("type") or "unstyled"
        try:
            depth = int(b.get("depth") or 0)
        except (TypeError, ValueError):
            depth = 0
        prefix = _LIST_MARKERS.get(str(block_type), "")
        indent = "  " * depth if prefix else ""
        lines.append(f"{indent}{prefix}{text}")
    return "\n".join(lines)


def _draftjs_from_parsed_object(obj: Any) -> str:
    if isinstance(obj, str):
        return _string_to_draftjs_plain(obj)
    if isinstance(obj, list):
        if not obj:
            return ""
        if all(isinstance(b, dict) and ("text" in b or "type" in b) for b in obj):
            return _blocks_to_lines(obj)
        if len(obj) == 1:
            return parse_draftjs(obj[0])
        return "\n\n".join(parse_draftjs(x) for x in obj)
    if not isinstance(obj, dict):
        return ""
    blocks = _get_blocks_list(obj)
    if isinstance(blocks, list):
        return _blocks_to_lines(blocks)
    return ""


def _string_to_draftjs_plain(s: str) -> str:
    s = s.strip().replace("\ufeff", "")
    if not s:
        return ""
    tl = s.lstrip()
    if not (tl.startswith(("{", "[")) or "blocks" in s or '"text"' in s):
        return _strip_html(s).strip()
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, TypeError, ValueError):
        loose = _draftjs_loose_text_from_string(s)
        if loose:
            return re.sub(r"\s+", " ", loose).strip()
        tl2 = s.lstrip()
        if (tl2.startswith("{") and '"blocks"' in s) or (tl2.startswith("[") and '"blocks"' in s):
            return ""
        return _strip_html(s).strip()
    return _draftjs_from_parsed_object(obj)


def parse_draftjs(raw: Any) -> str:
    """Convert a Draft.js JSON string, dict, or list to plaintext.

    protocols.io may return ``description`` as a JSON *string* (valid Draft.js),
    a pre-parsed dict, or (rarely) malformed text that is still recognizably
    Draft. Never raises.

    * ``/protocol-sources`` and this pipeline use the same logic so the UI and
    LLM see matching plaintext.
    """
    if raw is None:
        return ""
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8", errors="replace")
        except Exception:
            return ""
    if isinstance(raw, str):
        return _string_to_draftjs_plain(raw)
    if isinstance(raw, list):
        return _draftjs_from_parsed_object(raw)
    if isinstance(raw, dict):
        return _draftjs_from_parsed_object(raw)
    return _strip_html(str(raw)).strip()


# ---------------------------------------------------------------------------
# Language detection — heuristic only, no extra deps
# ---------------------------------------------------------------------------

# Spanish stopwords + diacritic markers. Cheap detector: if any of these
# appear in the first ~500 chars, call it Spanish. Good enough to flag the
# trehalose Spanish protocol; we don't need 100% accuracy because the LLM
# can translate from any language regardless of how we label it. The flag
# just lets prompts say "this source is Spanish, translate inline".
_SPANISH_TOKENS = (
    "mé", "á", "é", "í", "ó", "ú", "ñ", "¿", "¡",
    " del ", " los ", " las ", " que ", " con ", " para ",
    " por ", " est", " son ", " una ", " un ",
)


def detect_language(text: str) -> str:
    """Return 'es' for likely Spanish, 'en' otherwise. 'unknown' for empty."""
    if not text:
        return "unknown"
    sample = text[:500].lower()
    if any(tok in sample for tok in _SPANISH_TOKENS):
        return "es"
    return "en"


# ---------------------------------------------------------------------------
# Top-level: load + normalize a sample bundle
# ---------------------------------------------------------------------------

def _author_names(creator: Any, authors: Any) -> list[str]:
    """protocols.io's search items have both `creator` (single object) and
    `authors` (list of objects with `name`). Prefer `authors`; fall back to
    `creator`. Returns plain string names."""
    out: list[str] = []
    if isinstance(authors, list):
        for a in authors:
            if isinstance(a, dict):
                name = (a.get("name") or "").strip()
                if name:
                    out.append(name)
    if not out and isinstance(creator, dict):
        name = (creator.get("name") or creator.get("username") or "").strip()
        if name:
            out.append(name)
    return out


def normalize_bundle(bundle: dict) -> Optional[NormalizedProtocol]:
    """Take a raw `pipeline_output_samples/protocols_io/<name>.json` dict
    and return its top hit as a NormalizedProtocol. Returns None if the
    bundle has no usable search results."""
    items = (bundle.get("search") or {}).get("items") or []
    if not items:
        return None
    item = items[0]
    proto_id = str(item.get("id") or "")

    # Steps come back nested as top_hit_steps.payload (a list of step objects)
    raw_steps = ((bundle.get("top_hit_steps") or {}).get("payload")) or []
    steps: list[NormalizedStep] = []
    for raw in raw_steps:
        if not isinstance(raw, dict):
            continue
        body = parse_draftjs(raw.get("step"))
        if not body:
            continue  # skip blank steps (some sections contain only headers)
        steps.append(NormalizedStep(
            id=str(raw.get("id") or raw.get("guid") or ""),
            section=_strip_html(raw.get("section") or ""),
            number=str(raw.get("number") or ""),
            text=body,
            duration_seconds=raw.get("duration") if isinstance(raw.get("duration"), int) else None,
        ))

    materials_text = parse_draftjs(item.get("materials_text"))

    # Use steps + materials_text + description for language detection so a
    # protocol with English title but Spanish body gets flagged correctly.
    detect_corpus = " ".join([
        materials_text,
        " ".join(s.text for s in steps[:3]),
    ])
    language = detect_language(detect_corpus)

    return NormalizedProtocol(
        id=proto_id,
        title=_strip_html(item.get("title") or item.get("title_html") or ""),
        description=item.get("description") or None,
        doi=item.get("doi") or None,
        url=item.get("url") or item.get("link") or None,
        authors=_author_names(item.get("creator"), item.get("authors")),
        language=language,
        materials_text=materials_text,
        steps=steps,
    )


def load_sample(name: str) -> Optional[NormalizedProtocol]:
    """Load a normalized protocol from
    `pipeline_output_samples/protocols_io/<name>.json`."""
    path = SAMPLES_DIR / f"{name}.json"
    bundle = json.loads(path.read_text(encoding="utf-8"))
    return normalize_bundle(bundle)


def load_all_samples() -> dict[str, NormalizedProtocol]:
    """Load every available sample. Skips bundles that produce no normalized
    protocol (empty search results)."""
    out: dict[str, NormalizedProtocol] = {}
    if not SAMPLES_DIR.exists():
        return out
    for path in sorted(SAMPLES_DIR.glob("*.json")):
        bundle = json.loads(path.read_text(encoding="utf-8"))
        norm = normalize_bundle(bundle)
        if norm is not None:
            out[path.stem] = norm
    return out
