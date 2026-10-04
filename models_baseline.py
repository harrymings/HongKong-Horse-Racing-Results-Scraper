import pickle
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier

MODEL_PATH = Path(__file__).parent / "model.pkl"


def train_baseline(df: pd.DataFrame, target_col: str = "is_win"):
    if target_col not in df.columns:
        raise ValueError(f"Target column {target_col!r} is missing")
    if "race_date" not in df.columns:
        raise ValueError("Training data must include race_date for chronological ordering")

    df = df.dropna(subset=[target_col]).sort_values(["race_date", "race_id"] if "race_id" in df.columns else ["race_date"])
    excluded = {target_col, "race_date", "race_id", "horse_id", "runner_id", "finishing_pos", "finishing_time", "margin", "win_odds"}
    feature_columns = [
        column for column in df.select_dtypes(include="number").columns
        if column not in excluded and column != "is_win"
    ]
    if not feature_columns:
        raise ValueError("Training data has no usable numeric pre-race features")

    X = df[feature_columns].replace([float("inf"), float("-inf")], pd.NA)
    fill_values = X.median().fillna(0.0)
    X = X.fillna(fill_values)
    y = df[target_col].astype(int)
    if y.nunique() < 2:
        raise ValueError("Training target must contain both winners and non-winners")

    model = GradientBoostingClassifier(n_estimators=50)
    model.fit(X, y)
    artifact = {
        "model": model,
        "feature_columns": feature_columns,
        "fill_values": fill_values.to_dict(),
        "target_col": target_col,
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }
    MODEL_PATH.write_bytes(pickle.dumps(artifact))
    return MODEL_PATH


def train_from_scraped_data():
    from feature_engineering import example_pipeline

    features = example_pipeline()
    if features is None:
        raise ValueError("No usable race JSON data found. Run the scraper first.")
    return train_baseline(pd.DataFrame(features.to_dicts()))


if __name__ == "__main__":
    try:
        print(f"Saved baseline model to {train_from_scraped_data()}")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
