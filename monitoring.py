import json
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


def check_duplicate_races():
    import glob
    files = glob.glob(str(DATA_DIR / "race_*.json"))
    seen = set()
    dup = []
    for f in files:
        j = json.loads(open(f).read())
        rid = j.get("race_id")
        if rid in seen:
            dup.append(f)
        else:
            seen.add(rid)
    return dup


def check_incomplete_runners():
    import glob
    files = glob.glob(str(DATA_DIR / "race_*.json"))
    bad = []
    for f in files:
        j = json.loads(open(f).read())
        for r in j.get("runners", []):
            if not r.get("horse_id"):
                bad.append((j.get("race_id"), r))
    return bad
