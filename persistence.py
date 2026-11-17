"""Atomic, conservative persistence for collected source records."""
import json
import os
import tempfile
from pathlib import Path


def atomic_text(path, content, encoding="utf-8"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding=encoding, dir=path.parent,
                                         prefix="." + path.name, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


def merge_non_null(old, new):
    """Keep collected values when a refresh has no replacement; match runners by ID.

    Explicit corrections remain possible. Empty collections are not deletions.
    """
    if new is None or new == [] or new == {}:
        return old if old is not None else new
    if isinstance(old, dict) and isinstance(new, dict):
        result = dict(old)
        for key, value in new.items():
            if key == 'race_time_weather':
                # This object is already merged against an exact target/station by its enricher.
                # Generic null-preservation would attach old weather to a changed race time.
                result[key] = value
                continue
            if key in {'weather', 'venue_weather'} and isinstance(old.get(key), dict) and isinstance(value, dict):
                value = merge_weather(old[key], value)
            result[key] = merge_non_null(old.get(key), value)
        return result
    if isinstance(new, list) and new and all(isinstance(r, dict) and r.get("runner_id") for r in new):
        previous = {r["runner_id"]: r for r in old or []}
        # A smaller fetched roster must not silently remove historical entrants.
        result = [merge_non_null(previous.pop(r["runner_id"], {}), r) for r in new]
        return result + list(previous.values())
    return new


def save_race(path, race):
    path = Path(path)
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    merged = merge_non_null(old, race)
    old_ids = {r.get('runner_id') for r in old.get('runners', [])}
    fresh_ids = {r.get('runner_id') for r in race.get('runners', [])}
    if old_ids - fresh_ids and fresh_ids:
        merged['roster_refresh_status'] = 'MISMATCH_PRESERVED_HISTORY'
        merged['source_result_runner_count'] = len(fresh_ids)
    atomic_json(path, merged)
    return merged


def merge_weather(old, fresh):
    """A failed field refresh cannot replace provenance of a retained observation."""
    from copy import deepcopy
    fresh = deepcopy(fresh)
    for field, value in old.items():
        if field.endswith(('_c', '_pct', '_mm', '_hpa')) and value is not None and (
                fresh.get(field) is None or
                (old.get('field_sources',{}).get(field,{}).get('status') == 'AVAILABLE' and
                 fresh.get('field_sources',{}).get(field,{}).get('status') not in {None,'AVAILABLE'}) or
                (old.get('fetched_at') and fresh.get('fetched_at') and old['fetched_at'] > fresh['fetched_at'])):
            fresh[field] = value
            if field in old.get('field_sources', {}):
                fresh.setdefault('field_sources', {})[field] = old['field_sources'][field]
    return fresh
