import polars as pl
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).parent / "data"


def load_runners_parquet(parquet_path: str):
    return pl.read_parquet(parquet_path)


def compute_rolling_winrate(runners_df: pl.DataFrame, window: int = 50) -> pl.DataFrame:
    required = {"horse_id", "race_date", "finishing_pos"}
    missing = required.difference(runners_df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")
    if window < 1:
        raise ValueError("window must be at least 1")

    # Only earlier meeting dates enter history. Same-day starts cannot leak across rows.
    import pandas as pd
    frame = pd.DataFrame(runners_df.to_dicts()).sort_values(["horse_id", "race_date"])
    rows=[]
    for horse, group in frame.groupby("horse_id", sort=False):
        history=[]
        for day, daily in group.groupby("race_date", sort=True):
            prior=history[-window:]
            for record in daily.to_dict("records"):
                record.update(is_win=int(record.get("finishing_pos")==1),starts_before=len(history),
                              **{f"win_sum_{window}":sum(prior), f"win_rate_{window}":sum(prior)/len(prior) if prior else 0.})
                rows.append(record)
            history.extend(int(p==1) for p in daily["finishing_pos"].dropna())
    return pl.DataFrame(rows)


def example_pipeline() -> Optional[pl.DataFrame]:
    import json

    files = sorted(DATA_DIR.glob("race_*.json"))
    rows = []
    for f in files:
        j = json.loads(f.read_text())
        for r in j.get("runners", []):
            race_date = j.get("meeting_date") or (j.get("scraped_at") or "")[:10]
            if not r.get("horse_id") or not race_date:
                continue
            rows.append({
                "horse_id": r["horse_id"],
                "race_id": j["race_id"],
                "race_date": race_date,
                "finishing_pos": r.get("finishing_pos"),
                "draw": r.get("draw"),
                "actual_weight": r.get("actual_weight"),
                "declared_horse_weight": r.get("declared_horse_weight"),
                "distance_m": j.get("distance_m"),
            })
    if not rows:
        return None
    return compute_rolling_winrate(pl.DataFrame(rows))
