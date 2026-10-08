import json

from clearance.ingest.chunker import chunk

docs = {}
with open("data/org_nist/documents.jsonl") as fh:
    for line in fh:
        d = json.loads(line)
        docs[d["id"]] = d

with open("data/org/quarantine.jsonl") as fh:
    for item in (json.loads(line) for line in fh):
        doc = docs[item["doc_id"]]
        text = chunk(doc["text"])[item["ord"]]
        print("scan result:", {k: v for k, v in item.items() if k not in ("doc_id", "ord")})
        print(
            "document:", item["doc_id"], "-", doc["title"],
            f"(chunk {item['ord']}, {len(text)} chars)",
        )
        print("-" * 60)
        print(text[:1500])