import re
import json
import hashlib
import os
import tempfile
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Optional

MAPPING_FILE = Path(__file__).parent / "mappings" / "entity_mappings.json"
MAPPING_FILE.parent.mkdir(parents=True, exist_ok=True)


def normalize_name(name: str) -> str:
    if not name:
        return ""
    s = name.lower()
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def canonical_id_for(entity_type: str, name: str, extra: Optional[Dict] = None) -> str:
    norm = normalize_name(name)
    base = f"{entity_type}:{norm}"
    if extra:
        base += ":" + json.dumps(extra, sort_keys=True)
    return hashlib.sha1(base.encode("utf8")).hexdigest()


def load_mappings() -> Dict:
    if MAPPING_FILE.exists():
        return json.loads(MAPPING_FILE.read_text())
    return {}


def save_mappings(mappings: Dict):
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=MAPPING_FILE.parent,
                                         prefix=".entity_mappings_", suffix=".tmp", delete=False) as temp_file:
            temp_path = Path(temp_file.name)
            json.dump(mappings, temp_file, indent=2)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, MAPPING_FILE)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


@contextmanager
def _mapping_lock():
    with MAPPING_FILE.with_suffix(".lock").open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def map_entity(entity_type: str, raw_name: str, metadata: Optional[Dict] = None) -> Dict:
    with _mapping_lock():
        mappings = load_mappings()
        norm = normalize_name(raw_name)
        source_id = (metadata or {}).get("source_id")
        if source_id:
            source_id = source_id.upper()
            cid = hashlib.sha1(f"{entity_type}:hkjc:{source_id}".encode()).hexdigest()
            legacy = canonical_id_for(entity_type, raw_name)
            info = mappings.get(cid, {"entity_type": entity_type, "canonical_name": raw_name.strip(),
                                      "canonical_name_norm": norm, "aliases": [],
                                      "legacy_ids": [], "metadata": {"source_id": source_id}})
            info["aliases"] = sorted(set(info["aliases"] + [norm]))
            info["legacy_ids"] = sorted(set(info["legacy_ids"] + [legacy]))
            info["last_seen"] = datetime.now(timezone.utc).isoformat()
            mappings[cid] = info
            save_mappings(mappings)
            return {"canonical_id": cid, "canonical_name": info["canonical_name"], "legacy_id": legacy}
        for cid, info in mappings.items():
            if info.get("entity_type") == entity_type and not info.get("metadata", {}).get("source_id"):
                aliases = info.get("aliases", []) or []
                if norm in aliases or norm == info.get("canonical_name_norm"):
                    info["last_seen"] = datetime.now(timezone.utc).isoformat()
                    mappings[cid] = info
                    save_mappings(mappings)
                    return {"canonical_id": cid, "canonical_name": info.get("canonical_name")}

        cid = canonical_id_for(entity_type, raw_name, extra=metadata)
        mappings[cid] = {
            "entity_type": entity_type,
            "canonical_name": raw_name.strip(),
            "canonical_name_norm": norm,
            "aliases": [norm],
            "metadata": metadata or {},
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }
        save_mappings(mappings)
        return {"canonical_id": cid, "canonical_name": raw_name.strip()}
