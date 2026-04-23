from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from config import cfg_manager
from database import Database
import html

app = FastAPI()
db = Database(cfg_manager.config.db_filename)

class RecordPayload(BaseModel):
    secret: str
    name: str
    score: int
    duration: int

@app.on_event("startup")
async def startup():
    await db.init()

@app.post("/record")
async def add_record(payload: RecordPayload):
    if payload.secret != cfg_manager.config.api_secret:
        raise HTTPException(403, "Unauthorized")
    name = html.escape(payload.name[:20])
    await db.add_record(name, payload.score, payload.duration)
    # Уведомление админу (можно вызвать через бота, если он в том же цикле)
    return {"status": "ok"}

@app.get("/leaderboard")
async def leaderboard():
    records = await db.get_top_records()
    return [{"name": r["name"], "score": r["score"], "duration": r["duration"]} for r in records]