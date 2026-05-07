from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pathlib import Path
import pickle
from db import get_engine
from sqlalchemy import text
from dotenv import load_dotenv
import os

# Load environment variables from .env if present
load_dotenv()
os.environ.update(os.environ)

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
        # fallback: return uniform probabilities
        return {"race_id": req.race_id, "predictions": []}
    model = pickle.loads(open(model_path, "rb").read())
    # user should implement feature extraction; return placeholder
    return {"race_id": req.race_id, "predictions": []}
