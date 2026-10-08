"""NIST smoke test: real LLM, real text, access-derived expectations.

Run (DATABASE_URL must point at clearance_nist):
    python scripts/smoke_nist.py
"""
import json
import os
import time
from pathlib import Path

import psycopg

from clearance.answering.answer import answer
from clearance.answering.llm import get_provider
from clearance.retrieval.search import search
from reference_resolver import authorised

ORG = Path("data/org_nist")
org = json.loads((ORG / "org.json").read_text())
personas = json.loads((ORG / "personas.json").read_text())
topic_to_doc = {}
for line in (ORG / "documents.jsonl").read_text().splitlines():
    d = json.loads(line)
    if d["topic"].startswith("nist-"):
        topic_to_doc[d["topic"]] = d["id"]

# (question, topic the answer lives in, what a correct answer says; eyeball this)
QUESTIONS = [
    ("How many control families does the security and privacy controls catalog define?",
     "nist-sp-800-53", "20 families"),
    ("What are the phases of the incident response life cycle?",
     "nist-sp-800-61", "preparation; detection and analysis; containment, eradication and recovery; post-incident activity"),
    ("What are the steps of the risk management framework?",
     "nist-sp-800-37", "prepare, categorize, select, implement, assess, authorize, monitor"),
    ("What are the three assurance levels in the digital identity guidelines?",
     "nist-sp-800-63-3", "identity (IAL), authenticator (AAL), federation (FAL)"),
    ("How many families of security requirements are defined for protecting controlled unclassified information?",
     "nist-sp-800-171", "14 families"),
]
PERSONAS = ["administrator", "security_analyst", "compliance_analyst", "mid_visibility"]

provider = get_provider()
print("provider:", provider.name, getattr(provider, "model", ""))
conn = psycopg.connect(os.environ["DATABASE_URL"])

leaks = answered = refused_ok = wrong_refusal = total_in = total_out = 0
for persona in PERSONAS:
    user = personas[persona]["user"]
    visible = authorised(org, user)
    for question, topic, expected in QUESTIONS:
        target = topic_to_doc[topic]
        can_see = target in visible
        hits = search(conn, user, question, top=5, mode="prefilter")
        out = answer(question, hits, provider)
        total_in += out.input_tokens
        total_out += out.output_tokens

        cited_hidden = [d for d in out.citations if d not in visible]
        if cited_hidden:
            verdict = "LEAK"
            leaks += 1
        elif can_see and not out.refused and target in out.citations:
            verdict = "OK-answered"
            answered += 1
        elif not can_see and out.refused:
            verdict = "OK-refused"
            refused_ok += 1
        elif can_see and out.refused:
            verdict = "CHECK-refused-but-visible"
            wrong_refusal += 1
        else:
            verdict = "CHECK"
        print(f"[{verdict}] {persona:<18} {topic:<17} sees={can_see!s:<5} cites={out.citations} reason={out.reason}")
        print(f"        expect: {expected}")
        print(f"        A: {out.text[:200]}")
        time.sleep(4)

n = len(PERSONAS) * len(QUESTIONS)
print(f"\nquestions: {n}  leaks: {leaks}  answered: {answered}  correctly refused: {refused_ok}  "
      f"refused-but-visible: {wrong_refusal}  tokens in/out: {total_in}/{total_out}")