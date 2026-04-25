"""File-based cache for external API responses. Keyed by SHA-256 of a
canonical request body. TTLs enforced on read."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

CACHE_DIR = Path(".cache")


def _key(namespace: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return f"{namespace}/{digest}.json"


def get(namespace: str, payload: dict[str, Any], ttl_seconds: int) -> Any | None:
    path = CACHE_DIR / _key(namespace, payload)
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > ttl_seconds:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def put(namespace: str, payload: dict[str, Any], value: Any) -> None:
    path = CACHE_DIR / _key(namespace, payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
