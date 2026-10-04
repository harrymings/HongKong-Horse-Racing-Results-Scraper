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

    df = runners_df.sort(["horse_id", "race_date", "race_id"] if "race_id" in runners_df.columns else ["horse_id", "race_date"])
    df = df.with_columns(
        pl.when(pl.col("finishing_pos") == 1).then(1).otherwise(0).cast(pl.Int8).alias("is_win")
    )
    return df.with_columns(
        pl.int_range(pl.len()).over("horse_id").alias("starts_before"),
        pl.col("is_win").shift(1).rolling_sum(window_size=window, min_samples=1).over("horse_id").fill_null(0).alias(f"win_sum_{window}"),
        pl.col("is_win").shift(1).rolling_mean(window_size=window, min_samples=1).over("horse_id").fill_null(0.0).alias(f"win_rate_{window}"),
    )


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
