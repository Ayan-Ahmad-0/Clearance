"""Live demo API. The browser sends a persona name and a question, never a user id."""
import json
import os
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from clearance.answering.answer import answer
from clearance.answering.llm import get_provider
from clearance.retrieval.search import search
from reference_resolver import authorised

ORG_PATH_ENV = os.environ.get("ORG_DIR", "data/org_nist")
ORG = Path(ORG_PATH_ENV)
if not ORG.is_absolute():
    repo_root = Path(__file__).resolve().parents[3]
    candidate = repo_root / ORG_PATH_ENV
    if candidate.exists():
        ORG = candidate

DSN = os.environ.get("DATABASE_URL", "")
ORIGINS_RAW = os.environ.get("ALLOWED_ORIGINS", "*")
ORIGINS = [o.strip() for o in ORIGINS_RAW.split(",") if o.strip()]
PER_IP = int(os.environ.get("RATE_PER_IP", "10"))   # questions per window per address
WINDOW = 600                                          # seconds
DAILY_CAP = int(os.environ.get("DAILY_CAP", "300"))   # questions per day, all visitors

PERSONAS = ["administrator", "security_analyst", "compliance_analyst", "mid_visibility"]
org = json.loads((ORG / "org.json").read_text(encoding="utf-8"))
people = json.loads((ORG / "personas.json").read_text(encoding="utf-8"))
titles = {}
for line in (ORG / "documents.jsonl").read_text(encoding="utf-8").splitlines():
    d = json.loads(line)
    titles[d["id"]] = d["title"]
visible = {p: authorised(org, people[p]["user"]) for p in PERSONAS}  # computed once
provider = get_provider()

app = FastAPI(title="Clearance demo API")
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS,
                   allow_methods=["GET", "POST"], allow_headers=["*"])

recent = defaultdict(deque)
daily = {"day": "", "n": 0}


def check_limits(ip: str) -> None:
    now = time.time()
    q = recent[ip]
    while q and now - q[0] > WINDOW:
        q.popleft()
    if len(q) >= PER_IP:
        raise HTTPException(429, "Too many questions from your address. Try again in a few minutes.")
    today = datetime.now(tz=UTC).date().isoformat()
    if daily["day"] != today:
        daily.update(day=today, n=0)
    if daily["n"] >= DAILY_CAP:
        raise HTTPException(429, "Daily demo limit reached. The example questions still work.")
    q.append(now)
    daily["n"] += 1


class Ask(BaseModel):
    persona: str
    question: str = Field(min_length=3, max_length=300)


@app.get("/health")
def health():
    db_ok = False
    if DSN:
        try:
            with psycopg.connect(DSN, connect_timeout=3) as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
                db_ok = True
        except (psycopg.Error, OSError):
            db_ok = False
    return {"ok": True, "database": db_ok, "model": getattr(provider, "model", provider.name)}


@app.post("/ask")
def ask(body: Ask, request: Request):
    if not DSN:
        raise HTTPException(503, "Database is not connected.")
    if body.persona not in visible:
        raise HTTPException(400, "Unknown person.")
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or request.client.host
    check_limits(ip)

    user, can_read = people[body.persona]["user"], visible[body.persona]
    out = {}
    with psycopg.connect(DSN) as conn:
        for mode in ("prefilter", "postfilter"):
            hits = search(conn, user, body.question.strip(), top=5, mode=mode)
            try:
                res = answer(body.question.strip(), hits, provider)
            except Exception as exc:  # model quota, network, bad key
                print("llm error:", repr(exc))
                raise HTTPException(502, "The language model is unavailable right now.") from exc
            bad = [h["doc_id"] for h in hits if h["doc_id"] not in can_read]
            bad += [d for d in res.citations if d not in can_read]
            if bad:
                print("ACCESS CHECK FAILED", body.persona, mode, bad)
                raise HTTPException(500, "Blocked: the access check failed.")
            out[mode] = {
                "refused": res.refused, "answer": res.text, "citations": res.citations,
                "hits": [{"doc_id": h["doc_id"], "title": titles.get(h["doc_id"], h["doc_id"]),
                          "snippet": " ".join(h["text"].split())[:240]} for h in hits],
            }
    return out


demo_dir = Path(__file__).resolve().parents[3] / "demo"
if demo_dir.is_dir() and (demo_dir / "index.html").exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(demo_dir), html=True), name="static")