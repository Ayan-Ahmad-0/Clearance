"""Precompute demo/demo.json. Run with DATABASE_URL -> clearance_nist,
CLEARANCE_LLM=gemini and GEMINI_API_KEY set:  python scripts/build_demo.py"""
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from clearance.answering.answer import answer
from clearance.answering.llm import get_provider
from clearance.retrieval.search import search
from reference_resolver import authorised

ORG = Path("data/org_nist")
org = json.loads((ORG / "org.json").read_text())
people = json.loads((ORG / "personas.json").read_text())
titles = {}
for line in (ORG / "documents.jsonl").read_text().splitlines():
    d = json.loads(line)
    titles[d["id"]] = d["title"]

PERSONAS = {
    "administrator": "Administrator (sees everything)",
    "security_analyst": "Security analyst",
    "compliance_analyst": "Compliance analyst (legal)",
    "mid_visibility": "Mid-visibility staff (no NIST access)",
}
QUESTIONS = [
    ("q1", "How many control families does the security and privacy controls catalog define?"),
    ("q2", "What are the phases of the incident response life cycle?"),
    ("q3", "What are the steps of the risk management framework?"),
    ("q4", "What are the three assurance levels in the digital identity guidelines?"),
    ("q5", "How many families of security requirements are defined for protecting controlled unclassified information?"),
    ("q6", "What is the purpose of information security continuous monitoring?"),
]
# Measured by scripts/run_eval.py on the synthetic organisation (as of 2026-10-07).
SCORECARD = [
    ["administrator", 1800, 1.000, 1.000, 10.0],
    ["new_joiner", 18, 1.000, 0.696, 0.8],
    ["junior", 78, 1.000, 0.557, 2.83],
    ["mid_visibility", 195, 1.000, 0.573, 5.7],
]

provider = get_provider()
conn = psycopg.connect(os.environ["DATABASE_URL"])
personas_out, runs = {}, {}
leaks = tin = tout = 0

for pkey, label in PERSONAS.items():
    user = people[pkey]["user"]
    visible = authorised(org, user)
    pool = conn.execute("SELECT count(*) FROM chunks WHERE doc_id = ANY(%s)",
                        (sorted(visible),)).fetchone()[0]
    personas_out[pkey] = {"label": label, "documents": len(visible), "chunks": pool}
    for qid, q in QUESTIONS:
        run = {}
        for mode in ("prefilter", "postfilter"):
            hits = search(conn, user, q, top=5, mode=mode)
            out = answer(q, hits, provider)
            bad = [h["doc_id"] for h in hits if h["doc_id"] not in visible]
            bad += [d for d in out.citations if d not in visible]
            leaks += bool(bad)
            tin += out.input_tokens
            tout += out.output_tokens
            run[mode] = {
                "refused": out.refused, "answer": out.text, "citations": out.citations,
                "hits": [{"doc_id": h["doc_id"], "title": titles.get(h["doc_id"], h["doc_id"]),
                          "snippet": " ".join(h["text"].split())[:240]} for h in hits],
            }
            time.sleep(4)
        runs[f"{pkey}|{qid}"] = run
        print(pkey, qid, "prefilter refused" if run["prefilter"]["refused"] else "prefilter answered")

if leaks:
    raise SystemExit(f"{leaks} leaks found; not writing demo.json")

total = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
demo = {
    "meta": {"built": datetime.now(tz=UTC).date().isoformat(),
             "model": getattr(provider, "model", provider.name), "chunks": total,
             "runs": len(runs) * 2, "leaks": leaks, "tokens_in": tin, "tokens_out": tout},
    "personas": personas_out,
    "questions": [{"id": i, "text": t} for i, t in QUESTIONS],
    "scorecard": SCORECARD,
    "runs": runs,
}
Path("demo").mkdir(exist_ok=True)
Path("demo/demo.json").write_text(json.dumps(demo, ensure_ascii=False, indent=1), encoding="utf-8")
print("wrote demo/demo.json")