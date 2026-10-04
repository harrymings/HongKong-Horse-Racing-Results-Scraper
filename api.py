from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pathlib import Path
import pickle
import pandas as pd
from db import get_engine
from sqlalchemy import text
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

app = FastAPI(title="HK Racing Data API")


class PredictRequest(BaseModel):
    race_id: str


def _query_db(query: str, params: dict = None):
    eng = get_engine()
    with eng.connect() as conn:
        res = conn.execute(text(query), params or {})
        try:
            return [dict(r._mapping) for r in res]
        except Exception:
            return []


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/races")
def list_races(limit: int = 100):
    q = "SELECT race_id, meeting_date, venue FROM races LIMIT :limit"
    try:
        return _query_db(q, {"limit": limit})
    except Exception:
        raise HTTPException(status_code=500, detail="DB query failed")


@app.get("/horse/{horse_id}")
def horse(horse_id: str):
    q = "SELECT * FROM horses WHERE horse_id = :horse_id"
    rows = _query_db(q, {"horse_id": horse_id})
    if not rows:
        raise HTTPException(status_code=404, detail="Not found")
    return rows[0]


@app.post("/predict")
def predict(req: PredictRequest):
    model_path = Path(__file__).parent / "model.pkl"
    if not model_path.exists():
        raise HTTPException(status_code=503, detail="No trained model found. Train a baseline model first.")
    try:
        with model_path.open("rb") as model_file:
            artifact = pickle.load(model_file)
    except (OSError, pickle.UnpicklingError, EOFError) as exc:
        raise HTTPException(status_code=500, detail="The saved model could not be loaded") from exc

    try:
        entries = _query_db(
            "SELECT r.runner_id, r.horse_id, r.draw, r.weight_carried AS actual_weight, "
            "r.declared_weight AS declared_horse_weight, race.distance_m "
            "FROM runners r JOIN races race ON race.race_id = r.race_id WHERE r.race_id = :race_id "
            "ORDER BY r.draw, r.runner_id",
            {"race_id": req.race_id},
        )
        if not entries:
            raise HTTPException(status_code=404, detail="No runners found for this race")
        history = _query_db(
            "SELECT past_runner.horse_id, past_runner.finishing_pos "
            "FROM runners past_runner JOIN races past_race ON past_race.race_id = past_runner.race_id "
            "WHERE past_runner.horse_id IN (SELECT horse_id FROM runners WHERE race_id = :race_id) "
            "AND past_race.meeting_date < (SELECT meeting_date FROM races WHERE race_id = :race_id) "
            "ORDER BY past_race.meeting_date, past_race.race_number",
            {"race_id": req.race_id},
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Could not load race features from the database") from exc

    history_by_horse = {}
    for result in history:
        history_by_horse.setdefault(result["horse_id"], []).append(result["finishing_pos"] == 1)

    feature_rows = []
    for entry in entries:
        past_results = history_by_horse.get(entry["horse_id"], [])[-50:]
        wins = sum(past_results)
        feature_rows.append({
            "runner_id": entry["runner_id"],
            "horse_id": entry["horse_id"],
            "draw": entry["draw"],
            "actual_weight": entry["actual_weight"],
            "declared_horse_weight": entry["declared_horse_weight"],
            "distance_m": entry["distance_m"],
            "starts_before": len(history_by_horse.get(entry["horse_id"], [])),
            "win_sum_50": wins,
            "win_rate_50": wins / len(past_results) if past_results else 0.0,
        })

    if isinstance(artifact, dict):
        model = artifact.get("model")
        feature_columns = artifact.get("feature_columns", [])
        fill_values = artifact.get("fill_values", {})
    else:
        model = artifact
        feature_columns = list(getattr(model, "feature_names_in_", []))
        fill_values = {}
    if model is None or not feature_columns:
        raise HTTPException(status_code=500, detail="The saved model does not include a usable feature schema")

    features = pd.DataFrame(feature_rows).reindex(columns=feature_columns)
    features = features.fillna({column: fill_values.get(column, 0.0) for column in feature_columns})
    try:
        probabilities = model.predict_proba(features)
        class_labels = list(model.classes_)
        win_index = class_labels.index(1) if 1 in class_labels else None
        predictions = []
        for row, probability in zip(feature_rows, probabilities):
            predictions.append({
                "runner_id": row["runner_id"],
                "horse_id": row["horse_id"],
                "win_probability": float(probability[win_index]) if win_index is not None else 0.0,
            })
    except Exception as exc:
        raise HTTPException(status_code=500, detail="The saved model could not score these race features") from exc
    return {"race_id": req.race_id, "predictions": predictions}
