"""Build the eval set and score the arms.

Run:  python scripts/run_eval.py            (builds eval/questions.jsonl if missing)
      python scripts/run_eval.py --rebuild
Needs: org loaded (load_org.py), chunks ingested (ingest_org.py), data/org/*.
Writes benchmarks/results/<date>/eval.json
"""
import argparse
import json
import os
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

import gen_org
import psycopg

from clearance.retrieval.search import search

TOP = 10
GATE_POINTS = 0.05
PARAPHRASES = (
    "What does the {tier} note from the {dept} department say about {subject}?",
    "I need {tier} guidance on {subject} in {dept}.",
)
BROAD = ("How do we handle {subject}?", "What is the process for {subject}?")


def build(docs, secrets, seed=42, n_keys=100, n_probes=40):
    rng = random.Random(seed)
    keyed = {}
    for d in docs:
        if d["dept"] != "all":
            keyed.setdefault((d["dept"], d["topic"], d["tier"]), []).append(d["id"])
    keys = sorted(keyed)
    rng.shuffle(keys)
    items = []
    for dept, topic, tier in keys[:n_keys]:
        for template in PARAPHRASES:
            question = template.format(tier=tier, dept=dept, subject=gen_org.TOPICS[topic])
            items.append({"id": f"q{len(items):03d}", "kind": "topic", "question": question,
                          "relevant": keyed[(dept, topic, tier)], "topic": topic})
    for doc_id in rng.sample(sorted(secrets), n_probes):
        codename = secrets[doc_id][1]
        items.append({"id": f"q{len(items):03d}", "kind": "probe", "topic": None,
                      "question": f"What is the approved reserve for {codename}?",
                      "relevant": [doc_id]})
    for topic in sorted({d["topic"] for d in docs}):
        for template in BROAD:
            items.append({"id": f"q{len(items):03d}", "kind": "broad", "topic": topic,
                          "question": template.format(subject=gen_org.TOPICS[topic]),
                          "relevant": sorted(d["id"] for d in docs if d["topic"] == topic)})
    return items


def load_questions(path, docs, secrets, rebuild):
    if path.exists() and not rebuild:
        return [json.loads(line) for line in path.read_text().splitlines()]
    items = build(docs, secrets)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(i) for i in items) + "\n")
    return items


def new_bucket():
    return {"questions": 0, "answerable": 0, "recall_sum": 0.0, "returned": 0,
            "doc_leaks": 0, "forbidden_hits": 0, "distractors": 0}


def score(conn, personas, questions, docs, secrets):
    by_topic = {}
    for d in docs:
        by_topic.setdefault(d["topic"], set()).add(d["id"])
    arms = {
        "prefilter": lambda user, q: search(conn, user, q, mode="prefilter", top=TOP),
        "postfilter": lambda user, q: search(conn, user, q, mode="postfilter", top=TOP),
        "always_refuse": lambda user, q: [],
    }
    rows = []
    for pname, info in personas.items():
        user = info["user"]
        allowed = {r[0] for r in conn.execute("SELECT doc_id FROM authorised_documents(%s)", (user,))}
        pool = conn.execute(
            "SELECT count(*) FROM chunks WHERE doc_id = ANY(%s)", (sorted(allowed),)
        ).fetchone()[0]
        forbidden = [s for d, ss in secrets.items() if d not in allowed for s in ss]
        for arm, fn in arms.items():
            acc = {k: new_bucket() for k in ("topic", "probe", "broad")}
            for q in questions:
                b = acc[q["kind"]]
                hits = fn(user, q["question"])
                relevant = set(q["relevant"]) & allowed
                b["questions"] += 1
                b["returned"] += len(hits)
                b["doc_leaks"] += sum(h["doc_id"] not in allowed for h in hits)
                b["forbidden_hits"] += sum(any(s in h["text"] for s in forbidden) for h in hits)
                if relevant:
                    found = {h["doc_id"] for h in hits[:TOP]} & relevant
                    denom = min(TOP, len(relevant)) if q["kind"] == "broad" else len(relevant)
                    b["answerable"] += 1
                    b["recall_sum"] += len(found) / denom
                    if q["kind"] == "topic":
                        b["distractors"] += len((by_topic[q["topic"]] & allowed) - set(q["relevant"]))
            t, p, br = acc["topic"], acc["probe"], acc["broad"]

            def avg(bucket, key):
                return round(bucket[key] / bucket["answerable"], 3) if bucket["answerable"] else None

            rows.append({
                "arm": arm, "persona": pname, "user": user, "pool_chunks": pool,
                "recall_at_10": avg(t, "recall_sum"), "answerable": t["answerable"],
                "broad_at_10": avg(br, "recall_sum"), "broad_answerable": br["answerable"],
                "broad_results": round(br["returned"] / br["questions"], 2),
                "avg_results": round(t["returned"] / t["questions"], 2),
                "avg_distractors_in_pool": round(t["distractors"] / t["answerable"], 1)
                if t["answerable"] else None,
                "doc_leaks": sum(a["doc_leaks"] for a in acc.values()),
                "forbidden_hits": sum(a["forbidden_hits"] for a in acc.values()),
                "probe_recall": avg(p, "recall_sum"), "probe_answerable": p["answerable"],
            })
    return rows


def gates(rows):
    def get(arm, persona, key):
        for r in rows:
            if r["arm"] == arm and r["persona"] == persona:
                return r[key]
        return None

    admin = get("prefilter", "administrator", "broad_at_10")
    out = {}
    for arm in ("prefilter", "postfilter", "always_refuse"):
        mid = get(arm, "mid_visibility", "broad_at_10")
        out[arm] = None if admin is None or mid is None else (admin - mid) <= GATE_POINTS
    out["no_leaks_anywhere"] = all(r["doc_leaks"] == 0 and r["forbidden_hits"] == 0 for r in rows)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--org-dir", default="data/org")
    parser.add_argument("--questions", default="eval/questions.jsonl")
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()

    org = Path(args.org_dir)
    docs = [json.loads(line) for line in (org / "documents.jsonl").read_text().splitlines()]
    secrets = json.loads((org / "secrets.json").read_text())
    personas = json.loads((org / "personas.json").read_text())
    questions = load_questions(Path(args.questions), docs, secrets, args.rebuild)

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        rows = score(conn, personas, questions, docs, secrets)

    g = gates(rows)
    header = (f"{'arm':<14}{'persona':<16}{'pool':>6}{'topic@10':>9}{'broad@10':>9}"
              f"{'results':>8}{'leaks':>6}{'forb':>6}")
    print(header)
    for r in rows:
        tr = "-" if r["recall_at_10"] is None else f"{r['recall_at_10']:.3f}"
        br = "-" if r["broad_at_10"] is None else f"{r['broad_at_10']:.3f}"
        print(f"{r['arm']:<14}{r['persona']:<16}{r['pool_chunks']:>6}{tr:>9}{br:>9}"
              f"{r['broad_results']:>8}{r['doc_leaks']:>6}{r['forbidden_hits']:>6}")
    print(f"\ngate (broad@10, mid_visibility within {GATE_POINTS * 100:.0f} points of administrator): "
          f"{ {k: v for k, v in g.items() if k != 'no_leaks_anywhere'} }")
    print(f"no leaks in any arm: {g['no_leaks_anywhere']}")

    out_dir = Path("benchmarks/results") / datetime.now(tz=UTC).date().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "eval.json").write_text(json.dumps({"gates": g, "rows": rows}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())