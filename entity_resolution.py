import re
import json
import hashlib
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
    MAPPING_FILE.write_text(json.dumps(mappings, indent=2))


def map_entity(entity_type: str, raw_name: str, metadata: Optional[Dict] = None) -> Dict:
    mappings = load_mappings()
    norm = normalize_name(raw_name)
    # Try existing aliases
    for cid, info in mappings.items():
        if info.get("entity_type") == entity_type:
            aliases = info.get("aliases", []) or []
            if norm in aliases or norm == info.get("canonical_name_norm"):
                info["last_seen"] = __import__("datetime").datetime.utcnow().isoformat()
                mappings[cid] = info
                save_mappings(mappings)
                return {"canonical_id": cid, "canonical_name": info.get("canonical_name")}

    # create new mapping
    cid = canonical_id_for(entity_type, raw_name, extra=metadata)
    mappings[cid] = {
        "entity_type": entity_type,
        "canonical_name": raw_name.strip(),
        "canonical_name_norm": norm,
        "aliases": [norm],
        "metadata": metadata or {},
        "last_seen": __import__("datetime").datetime.utcnow().isoformat(),
    }
    save_mappings(mappings)
    return {"canonical_id": cid, "canonical_name": raw_name.strip()}
