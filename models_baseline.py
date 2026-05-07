import pickle
from pathlib import Path
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit
from sklearn.ensemble import GradientBoostingClassifier

MODEL_PATH = Path(__file__).parent / "model.pkl"


def train_baseline(df: pd.DataFrame, target_col: str = "is_win"):
    # df must contain feature columns and a chronological index column named 'race_date'
    df = df.sort_values("race_date")
    X = df.drop(columns=[target_col])
    y = df[target_col]
    tscv = TimeSeriesSplit(n_splits=5)
    model = GradientBoostingClassifier(n_estimators=50)
    # naive single-fit
    model.fit(X, y)
    MODEL_PATH.write_bytes(pickle.dumps(model))
    return MODEL_PATH
