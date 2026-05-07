import polars as pl
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


def load_runners_parquet(parquet_path: str):
    return pl.read_parquet(parquet_path)


def compute_rolling_winrate(runners_df: pl.DataFrame, window: int = 50) -> pl.DataFrame:
    # Expect runners_df to have columns: horse_id, race_date, finishing_pos
    df = runners_df.sort(["horse_id", "race_date"]).with_columns(
        (pl.col("finishing_pos").apply(lambda x: 1 if x == 1 else 0)).alias("is_win")
    )
    out = df.groupby("horse_id").agg(
        pl.col("is_win").rolling_sum(window_size=window).alias(f"win_sum_{window}"),
        pl.col("is_win").rolling_mean(window_size=window).alias(f"win_rate_{window}")
    )
    return out


def example_pipeline():
    # simple example reading all runner JSON files saved by scraper
    import json, glob
    files = glob.glob(str(DATA_DIR / "race_*.json"))
    rows = []
    for f in files:
        j = json.loads(open(f).read())
        for r in j.get("runners", []):
            rows.append({
                "horse_id": r["horse_id"],
                "race_id": j["race_id"],
                "race_date": j.get("scraped_at"),
                "finishing_pos": r.get("finishing_pos", None)
            })
    if not rows:
        return None
    df = pl.DataFrame(rows)
    feats = compute_rolling_winrate(df)
    return feats
