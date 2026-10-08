"""Overlay: add the seven NIST SP 800 PDFs to the synthetic organisation.

Run:  python scripts/add_nist.py --org data/org --nist data/nist --out data/org_nist
Leaves data/org untouched. ACLs are synthetic; the text is real public text.
"""
import argparse
import hashlib
import json
import random
import re
from collections import Counter, deque
from pathlib import Path

from pypdf import PdfReader

from reference_resolver import authorised

NIST = [
    # (filename regex, folder key, dept label, tier, topic, title)
    (r"800[-_. ]?53(?!\d)", "engineering", "security", "confidential", "nist-sp-800-53",
     "NIST SP 800-53 Rev 5: Security and Privacy Controls"),
    (r"800[-_. ]?171(?!\d)", "engineering", "security", "confidential", "nist-sp-800-171",
     "NIST SP 800-171 Rev 2: Protecting Controlled Unclassified Information"),
    (r"800[-_. ]?37(?!\d)", "governance", "legal", "confidential", "nist-sp-800-37",
     "NIST SP 800-37 Rev 2: Risk Management Framework"),
    (r"800[-_. ]?137(?!\d)", "governance", "legal", "confidential", "nist-sp-800-137",
     "NIST SP 800-137: Continuous Monitoring"),
    (r"800[-_. ]?30(?!\d)", "general", "security", "internal", "nist-sp-800-30",
     "NIST SP 800-30 Rev 1: Guide for Conducting Risk Assessments"),
    (r"800[-_. ]?63(?!\d)", "general", "security", "internal", "nist-sp-800-63-3",
     "NIST SP 800-63-3: Digital Identity Guidelines"),
    (r"800[-_. ]?61(?!\d)", "incident", "security", "restricted", "nist-sp-800-61",
     "NIST SP 800-61 Rev 2: Computer Security Incident Handling Guide"),
]
FOLDERS = ["general", "engineering", "governance", "incident"]


def gid(meta, name):
    for g, m in meta.items():
        if m["name"] == name:
            return g
    raise SystemExit(f"group {name!r} not found in groups_meta")


def reach(edges, user):
    seen, queue = set(), deque([user])
    while queue:
        for parent in edges.get(queue.popleft(), ()):
            if parent not in seen:
                seen.add(parent)
                queue.append(parent)
    return seen


def extract(path):
    pages = [(p.extract_text() or "").splitlines() for p in PdfReader(str(path)).pages]
    freq = Counter(x for lines in pages for x in {l.strip() for l in lines} if x)
    limit = 0.3 * len(pages)  # lines repeated on many pages are running headers/footers
    out = []
    for lines in pages:
        keep = []
        for line in lines:
            s = line.strip()
            if not s or freq[s] > limit or re.fullmatch(r"\d+|[ivxlc]+", s, re.IGNORECASE):
                continue
            keep.append(s)
        out.append("\n".join(keep))
    text = "\n\n".join(p for p in out if p)
    return re.sub(r"-\n(?=[a-z])", "", text)  # undo hyphenation at line ends


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", default="data/org")
    ap.add_argument("--nist", default="data/nist")
    ap.add_argument("--out", default="data/org_nist")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    src, out = Path(args.org), Path(args.out)
    rng = random.Random(args.seed + 1)

    org = json.loads((src / "org.json").read_text())
    meta = org["groups_meta"]
    graph = {k: org[k] for k in ("users", "groups", "member_of", "node_parent", "acl")}
    old_graph = json.loads(json.dumps(graph))
    docs = [json.loads(l) for l in (src / "documents.jsonl").read_text().splitlines()]
    personas = json.loads((src / "personas.json").read_text())

    root = next(n for n, p in graph["node_parent"].items() if p is None and n.startswith("F"))
    n_folder = 1 + max(int(n[1:]) for n in graph["node_parent"] if n.startswith("F"))
    n_doc = 1 + max(int(n[1:]) for n in graph["node_parent"] if n.startswith("d"))

    def new_folder(parent):
        nonlocal n_folder
        node = f"F{n_folder:03d}"
        n_folder += 1
        graph["node_parent"][node] = parent
        return node

    nist_root = new_folder(root)
    folder = {k: new_folder(nist_root) for k in FOLDERS}
    acl = graph["acl"]
    for g in ("div-3",):
        acl.append([folder["general"], gid(meta, g), "allow"])
    for g in ("dept-security", "dept-engineering"):
        acl.append([folder["engineering"], gid(meta, g), "allow"])
    for g in ("dept-legal", "dept-security"):
        acl.append([folder["governance"], gid(meta, g), "allow"])
    acl.append([folder["governance"], gid(meta, "dept-procurement"), "deny"])
    acl.append([folder["incident"], gid(meta, "team-security-1"), "allow"])

    pdfs = sorted(Path(args.nist).glob("*.pdf"))
    new_docs = {}
    for pattern, key, dept, tier, topic, title in NIST:
        match = [p for p in pdfs if re.search(pattern, p.name)]
        if len(match) != 1:
            raise SystemExit(f"{topic}: expected 1 PDF matching {pattern}, found {[p.name for p in match]}")
        did = f"d{n_doc:04d}"
        n_doc += 1
        graph["node_parent"][did] = folder[key]
        text = extract(match[0])
        new_docs[topic] = did
        docs.append({"id": did, "folder": folder[key], "dept": dept, "tier": tier,
                     "topic": topic, "title": title, "text": text})
        print(f"{topic}: {did}, {len(text):,} characters from {match[0].name}")

    # Direct exceptions at depth 0 (deterministic).
    edges = {}
    for child, parent in graph["member_of"]:
        edges.setdefault(child, []).append(parent)
    sec_group = gid(meta, "dept-security")
    regular = [u for u in graph["users"][23:]]
    for u in rng.sample(regular, 3):
        acl.append([new_docs["nist-sp-800-61"], u, "allow"])
    in_security = [u for u in regular if sec_group in reach(edges, u)]
    for u in rng.sample(in_security, 2):
        acl.append([new_docs["nist-sp-800-53"], u, "deny"])

    # The three restricted personas must be unchanged and must not see any NIST document.
    nist_ids = set(new_docs.values())
    for name in ("new_joiner", "junior", "mid_visibility"):
        u = personas[name]["user"]
        new = authorised(graph, u)
        if new & nist_ids or (new - nist_ids) != authorised(old_graph, u):
            raise SystemExit(f"persona {name} ({u}) changed or sees NIST; tell Claude, do not continue")

    # Two new personas that do see NIST.
    taken = {p["user"] for p in personas.values()}
    team1 = gid(meta, "team-security-1")
    legal = gid(meta, "dept-legal")
    for name, group in (("security_analyst", team1), ("compliance_analyst", legal)):
        u = next(x for x in regular if x not in taken and group in reach(edges, x)
                 and authorised(graph, x) - authorised(old_graph, x) != set())
        taken.add(u)
        vis = authorised(graph, u)
        personas[name] = {"user": u, "visible_documents": len(vis),
                          "visible_fraction": round(len(vis) / len(docs), 4),
                          "nist_documents": sorted(vis & nist_ids)}
    out.mkdir(parents=True, exist_ok=True)
    org_out = {**graph, "groups_meta": meta}
    (out / "org.json").write_text(json.dumps(org_out, indent=1))
    with (out / "documents.jsonl").open("w") as fh:
        for d in docs:
            fh.write(json.dumps(d) + "\n")
    (out / "secrets.json").write_text((src / "secrets.json").read_text())
    (out / "personas.json").write_text(json.dumps(personas, indent=1))
    sha = hashlib.sha256(json.dumps(org_out, sort_keys=True).encode()).hexdigest()
    print("personas:", json.dumps({k: v["user"] for k, v in personas.items()}))
    print("org_nist sha256:", sha)


if __name__ == "__main__":
    main()