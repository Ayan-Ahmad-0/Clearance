"""Smoke test: real LLM, a handful of questions, answer-level leak check.

Run:  python scripts/smoke_llm.py
Needs CLEARANCE_LLM=gemini and GEMINI_API_KEY in the environment.
"""
import json
import os
import time
from pathlib import Path

import psycopg

from clearance.answering.answer import answer
from clearance.answering.llm import get_provider
from reference_resolver import authorised

ORG = Path("data/org")
org = json.loads((ORG / "org.json").read_text())
personas = json.loads((ORG / "personas.json").read_text())
secrets = json.loads((ORG / "secrets.json").read_text())  # doc_id -> [canary, "Project X"]
docs = {}
for line in (ORG / "documents.jsonl").read_text().splitlines():
    d = json.loads(line)
    docs[d["id"]] = d

provider = get_provider()
print("provider:", provider.name, getattr(provider, "model", ""))
conn = psycopg.connect(os.environ["DATABASE_URL"])


def get_hits(user, question, k=5):
    from clearance.retrieval.search import search
    return search(conn, user, question, top=k, mode="prefilter")


def ask(persona, question, kind):
    user = personas[persona]["user"]
    visible = authorised(org, user)
    hidden_secrets = [s for did, ss in secrets.items() if did not in visible for s in ss]

    hits = get_hits(user, question)
    out = answer(question, hits, provider)

    leaked_string = [s for s in hidden_secrets if s in out.text]
    cited_hidden = [d for d in out.citations if d not in visible]
    ok_leak = not leaked_string and not cited_hidden
    status = "OK" if ok_leak else "LEAK"
    print(f"[{status}] {kind:<12} {persona:<15} refused={out.refused!s:<5} "
          f"reason={out.reason:<20} cites={out.citations} tok={out.input_tokens}/{out.output_tokens}")
    print(f"        Q: {question}")
    print(f"        A: {out.text[:160]}")
    time.sleep(4)  # stay under free-tier rate limits
    return ok_leak, out


results = []

# 1. Topic questions that each persona can answer (answer and cite expected).
for persona in ("administrator", "junior", "mid_visibility", "new_joiner"):
    user = personas[persona]["user"]
    visible = authorised(org, user)
    doc = docs[min(visible)]
    q = f"What does the {doc['dept']} department do about {doc['topic'].replace('-', ' ')}?"
    results.append(("topic", persona, *ask(persona, q, "topic")))

# 2. Secret probes: ask about a restricted document's codename.
restricted = [d for d in secrets]
admin_visible = authorised(org, personas["administrator"]["user"])
probe_doc = restricted[0]
codename = secrets[probe_doc][1]
q = f"What is the approved reserve for {codename}?"
for persona in ("administrator", "new_joiner", "junior", "mid_visibility"):
    results.append(("probe", persona, *ask(persona, q, "secret-probe")))

leaks = [r for r in results if not r[2]]
print("\nquestions:", len(results), " leaks:", len(leaks))