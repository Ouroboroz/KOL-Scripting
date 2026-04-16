import json
import logging
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

TIMESTAMP_KEY = "_cached_at"


# ── JSON cache (human-readable, for small/inspectable data) ──────────────────

def save_cache(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {TIMESTAMP_KEY: datetime.now(timezone.utc).isoformat(), **data}
    path.write_text(json.dumps(payload, indent=2))
    log.info("Saved cache to %s", path)


def load_cache(path: Path, ttl_hours: float) -> dict | None:
    if not path.exists():
        log.debug("Cache miss (file not found): %s", path)
        return None

    payload = json.loads(path.read_text())
    cached_at = datetime.fromisoformat(payload[TIMESTAMP_KEY])
    age_hours = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600

    if age_hours > ttl_hours:
        log.info("Cache stale (%.1fh old, TTL %.1fh): %s", age_hours, ttl_hours, path)
        return None

    log.info("Cache hit (%.1fh old): %s", age_hours, path)
    return {k: v for k, v in payload.items() if k != TIMESTAMP_KEY}


# ── Pickle cache (fast binary, for large Python objects like item graphs) ─────

def save_pickle(obj: Any, path: Path) -> None:
    """Pickle obj with a UTC timestamp wrapper. Faster than JSON for large graphs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"_cached_at": datetime.now(timezone.utc).isoformat(), "payload": obj}
    with open(path, "wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    log.info("Saved pickle cache to %s", path)


def load_pickle(path: Path, ttl_hours: float) -> Any | None:
    """Load pickled object if fresh (within TTL). Returns None if missing or stale."""
    if not path.exists():
        log.debug("Pickle cache miss (file not found): %s", path)
        return None
    try:
        with open(path, "rb") as f:
            payload = pickle.load(f)
    except Exception as exc:
        log.warning("Pickle cache corrupt, ignoring: %s (%s)", path, exc)
        return None

    cached_at = datetime.fromisoformat(payload["_cached_at"])
    age_hours = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600

    if age_hours > ttl_hours:
        log.info("Pickle cache stale (%.1fh old, TTL %.1fh): %s", age_hours, ttl_hours, path)
        return None

    log.info("Pickle cache hit (%.1fh old): %s", age_hours, path)
    return payload["payload"]


def cache_age_hours(path: Path) -> float | None:
    """Return age in hours of a pickle or JSON cache file, or None if missing."""
    if not path.exists():
        return None
    try:
        if path.suffix == ".pkl":
            with open(path, "rb") as f:
                payload = pickle.load(f)
            cached_at = datetime.fromisoformat(payload["_cached_at"])
        else:
            payload = json.loads(path.read_text())
            cached_at = datetime.fromisoformat(payload[TIMESTAMP_KEY])
        return (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600
    except Exception:
        return None
