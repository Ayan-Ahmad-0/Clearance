"""Seeded synthetic organisation for Clearance. MIT with the repository.

Run:  python scripts/gen_org.py --seed 42 --out data/org

Everything here is generated. No real people, companies or documents appear.
The access graph follows docs/resolver_spec.md. The proportions that the
differential test depends on (conflicts, cycles, over-bound chains) are
measured by `analyse` and checked in `check`, and the run fails if the
corpus is too easy.
"""
import argparse
import hashlib
import json
import random
from collections import deque
from itertools import pairwise
from pathlib import Path

N_USERS = 400
N_DOCS = 1800
N_ONBOARDING = 18
N_NEW_JOINERS = 20
N_ADMINS = 3
DEPTH_BOUND = 4
TEAMS_PER_DEPT = 3
TIERS = ("public", "internal", "confidential", "restricted")
TIER_WEIGHTS = (0.15, 0.45, 0.28, 0.12)

# (business unit index, departments)
DIVISIONS = [
    (0, ["finance", "legal", "procurement"]),
    (0, ["people", "facilities", "operations"]),
    (1, ["engineering", "security", "data"]),
    (1, ["sales", "marketing", "support"]),
]

TOPICS = {
    "expense-claims": "expense claims",
    "vendor-onboarding": "vendor onboarding requests",
    "incident-response": "incident reports",
    "access-reviews": "access review findings",
    "data-retention": "data retention exceptions",
    "travel-booking": "travel bookings",
    "contract-renewal": "contract renewals",
    "hardware-requests": "hardware requests",
    "training-compliance": "training completions",
    "release-approval": "release approvals",
    "customer-escalation": "customer escalations",
    "budget-variance": "budget variance notes",
    "audit-follow-up": "audit follow-up actions",
    "onboarding-checklist": "new starter checklists",
    "supplier-risk": "supplier risk assessments",
}
ROLES = [
    "department head",
    "controller",
    "operations lead",
    "compliance officer",
    "programme manager",
]
SYLLABLES = ["vel", "ora", "quil", "feath", "mar", "den", "tor", "wick",
             "sol", "brae", "nim", "kes", "lun", "pyr", "jod", "tal"]


def ancestors(node_parent, node):
    chain = []
    cur = node
    while cur is not None:
        chain.append(cur)
        cur = node_parent.get(cur)
    return chain


def make_text(rng, dept, tier, topic, ref):
    subject = TOPICS[topic]
    role = rng.choice(ROLES)
    n_days = rng.randint(2, 30)
    n_weeks = rng.randint(2, 12)
    amount = rng.randrange(500, 90000, 250)
    quarter = rng.randint(1, 4)
    middle = [
        (
            f"{subject.capitalize()} must be completed within {n_days} working days "
            f"and logged in the {dept} register."
        ),
        f"The {dept} team reviews open {subject} every {n_weeks} weeks and records any exception.",
        f"Approval for amounts above USD {amount:,} requires sign-off from the {role}.",
    ]
    rng.shuffle(middle)
    lines = [
        f"Reference {ref}. This {tier} note sets out how the {dept} department handles {subject}.",
        *middle,
        f"Owner: {role}. Last reviewed in Q{quarter}.",
    ]
    return " ".join(lines)


def build(seed=42):
    rng = random.Random(seed)
    groups = []
    meta = {}
    member_of = []

    def new_group(kind, name):
        gid = f"G{len(groups):03d}"
        groups.append(gid)
        meta[gid] = {"kind": kind, "name": name}
        return gid

    g_all = new_group("company", "all-staff")
    g_admin = new_group("company", "admins")
    bus = [new_group("business-unit", f"bu-{i + 1}") for i in range(2)]
    divs = []
    div_bu = {}
    depts = []
    teams = []
    for di, (bu_idx, dept_names) in enumerate(DIVISIONS):
        div = new_group("division", f"div-{di + 1}")
        member_of.append((div, bus[bu_idx]))
        divs.append(div)
        div_bu[div] = bus[bu_idx]
        for name in dept_names:
            dept = new_group("department", f"dept-{name}")
            member_of.append((dept, div))
            dept_teams = []
            for ti in range(TEAMS_PER_DEPT):
                team = new_group("team", f"team-{name}-{ti + 1}")
                member_of.append((team, dept))
                dept_teams.append(team)
                teams.append(team)
            depts.append({"name": name, "group": dept, "division": div, "teams": dept_teams})
    chain = [new_group("chain", f"chain-{i + 1}") for i in range(4)]

    # A chain of groups hangs under two departments, so members of those departments
    # reach groups deeper than the depth bound.
    chain_depts = rng.sample(depts, 2)
    for info in chain_depts:
        member_of.append((info["group"], chain[0]))
    for a, b in pairwise(chain):
        member_of.append((a, b))

    # Cycles: two 2-cycles between teams of different departments, one 3-cycle inside a department.
    cycle_depts = rng.sample(depts, 5)
    for pair in (cycle_depts[0:2], cycle_depts[2:4]):
        ta, tb = (rng.choice(info["teams"]) for info in pair)
        member_of.extend([(ta, tb), (tb, ta)])
    t1, t2, t3 = cycle_depts[4]["teams"]
    member_of.extend([(t1, t2), (t2, t3), (t3, t1)])

    users = [f"u{i:03d}" for i in range(N_USERS)]
    admins = users[:N_ADMINS]
    regular = users[N_ADMINS + N_NEW_JOINERS:]
    for u in users:
        member_of.append((u, g_all))
    for u in admins:
        member_of.append((u, g_admin))
    for u in regular:
        k = rng.choices([1, 2, 3], [55, 30, 15])[0]
        for team in rng.sample(teams, k):
            member_of.append((u, team))

    node_parent = {}
    n_folders = 0

    def new_folder(parent):
        nonlocal n_folders
        node = f"F{n_folders:03d}"
        n_folders += 1
        node_parent[node] = parent
        return node

    root = new_folder(None)
    onboarding = new_folder(root)
    for info in depts:
        info["folder"] = new_folder(root)
        info["tiers"] = {t: new_folder(info["folder"]) for t in TIERS}

    docs = []
    secrets = {}
    used_codenames = set()
    topic_names = sorted(TOPICS)

    def new_doc(folder, dept_name, tier):
        did = f"d{len(docs):04d}"
        node_parent[did] = folder
        topic = rng.choice(topic_names)
        text = make_text(rng, dept_name, tier, topic, f"{dept_name[:3].upper()}-{len(docs):04d}")
        if tier == "restricted":
            while True:
                code = "".join(rng.choice(SYLLABLES) for _ in range(3)).capitalize()
                if code not in used_codenames:
                    used_codenames.add(code)
                    break
            canary = f"CNRY-{rng.getrandbits(32):08x}"
            reserve = f"USD {rng.randint(10, 99)}.{rng.randint(0, 9)} million"
            text += f" Project {code}: approved reserve of {reserve}. Verification code {canary}."
            secrets[did] = [canary, f"Project {code}"]
        docs.append({"id": did, "folder": folder, "dept": dept_name, "tier": tier,
                     "topic": topic, "title": f"{dept_name} {topic} note {len(docs):04d}",
                     "text": text})

    for _ in range(N_ONBOARDING):
        new_doc(onboarding, "all", "public")
    for _ in range(N_DOCS - N_ONBOARDING):
        info = rng.choice(depts)
        tier = rng.choices(TIERS, TIER_WEIGHTS)[0]
        new_doc(info["tiers"][tier], info["name"], tier)

    acl = [(root, g_admin, "allow"), (onboarding, g_all, "allow")]
    for info in depts:
        tiers = info["tiers"]
        lead_a, lead_b, _ = info["teams"]
        acl += [
            (tiers["public"], info["division"], "allow"),
            (tiers["internal"], info["group"], "allow"),
            (tiers["confidential"], lead_a, "allow"),
            (tiers["confidential"], lead_b, "allow"),
            (tiers["restricted"], lead_a, "allow"),
        ]
    # Legal hold: one department per division has its internal folder denied to the whole division.
    for div in divs:
        held = rng.choice([i for i in depts if i["division"] == div])
        acl.append((held["tiers"]["internal"], div, "deny"))
    # Audit freeze: one department per business unit has its confidential folder denied to the unit.
    for bu in bus:
        held = rng.choice([i for i in depts if div_bu[i["division"]] == bu])
        acl.append((held["tiers"]["confidential"], bu, "deny"))
    # Rows on the chain: the last group is denied (applies at any depth), while an allow
    # on the same chain is out of bound for everyone and must never grant.
    for info in chain_depts:
        acl.append((info["tiers"]["internal"], chain[3], "deny"))
        acl.append((info["tiers"]["confidential"], chain[3], "allow"))
    # Direct exceptions at depth 0.
    restricted_docs = [d["id"] for d in docs if d["tier"] == "restricted"]
    for u in rng.sample(regular, 25):
        acl.append((rng.choice(restricted_docs), u, "allow"))
    for u in rng.sample(regular, 15):
        acl.append((rng.choice(docs)["id"], u, "deny"))

    graph = {
        "users": users,
        "groups": groups,
        "member_of": [list(e) for e in member_of],
        "node_parent": node_parent,
        "acl": [list(r) for r in acl],
    }
    return {"graph": graph, "documents": docs, "secrets": secrets, "groups_meta": meta}


def _bfs_depths(edges, start):
    depths = {}
    seen = {start}
    queue = deque([(start, 0)])
    while queue:
        node, d = queue.popleft()
        for parent in edges.get(node, ()):
            if parent not in seen:
                seen.add(parent)
                depths[parent] = d + 1
                queue.append((parent, d + 1))
    return depths


def _reaches_self(edges, start):
    """True if `start` can reach itself through one or more membership edges."""
    stack = list(edges.get(start, ()))
    seen = set()
    while stack:
        node = stack.pop()
        if node == start:
            return True
        if node not in seen:
            seen.add(node)
            stack.extend(edges.get(node, ()))
    return False


def analyse(built):
    """Measure the proportions the differential test depends on. Written separately
    from the resolver on purpose: it only needs depths and matched rows."""
    graph = built["graph"]
    docs = built["documents"]
    edges = {}
    for child, parent in graph["member_of"]:
        edges.setdefault(child, []).append(parent)
    rows = {}
    for node, subject, effect in graph["acl"]:
        rows.setdefault(node, []).append((subject, effect))
    node_parent = graph["node_parent"]
    chains = {d["id"]: ancestors(node_parent, d["id"]) for d in docs}
    cyclic = {g for g in graph["groups"] if _reaches_self(edges, g)}

    per_user = {}
    for u in graph["users"]:
        depths = _bfs_depths(edges, u)
        matched = {}
        for node, node_rows in rows.items():
            for subject, effect in node_rows:
                if subject == u:
                    matched.setdefault(node, []).append((effect, 0))
                elif subject in depths:
                    matched.setdefault(node, []).append((effect, depths[subject]))
        visible = []
        conflict = False
        for d in docs:
            allow_bounded = deny_any = deny_deep = False
            for node in chains[d["id"]]:
                for effect, depth in matched.get(node, ()):
                    if effect == "deny":
                        deny_any = True
                        deny_deep = deny_deep or depth >= 3
                    elif depth <= DEPTH_BOUND:
                        allow_bounded = True
            if allow_bounded and not deny_any:
                visible.append(d["id"])
            if allow_bounded and deny_deep:
                conflict = True
        per_user[u] = {
            "visible": visible,
            "conflict_depth3": conflict,
            "reaches_cycle": any(g in cyclic for g in depths),
            "beyond_bound": any(v > DEPTH_BOUND for v in depths.values()),
        }
    return per_user


def summarise(built, per_user):
    n = len(per_user)
    n_docs = len(built["documents"])
    fractions = {u: len(v["visible"]) / n_docs for u, v in per_user.items()}
    bands = {
        "up_to_2_percent": sum(f <= 0.02 for f in fractions.values()),
        "2_to_10_percent": sum(0.02 < f < 0.10 for f in fractions.values()),
        "10_to_25_percent": sum(0.10 <= f <= 0.25 for f in fractions.values()),
        "over_25_percent": sum(f > 0.25 for f in fractions.values()),
    }
    tier_counts = {t: sum(d["tier"] == t for d in built["documents"]) for t in TIERS}
    return {
        "principals": n,
        "groups": len(built["graph"]["groups"]),
        "folders": sum(1 for k in built["graph"]["node_parent"] if k.startswith("F")),
        "documents": n_docs,
        "documents_by_tier": tier_counts,
        "acl_rows": len(built["graph"]["acl"]),
        "share_users_with_conflict_depth3": sum(
            v["conflict_depth3"] for v in per_user.values()
        ) / n,
        "share_users_reaching_cycle": sum(v["reaches_cycle"] for v in per_user.values()) / n,
        "share_users_beyond_depth_bound": sum(v["beyond_bound"] for v in per_user.values()) / n,
        "users_per_visibility_band": bands,
        "admin_visible_fraction": fractions["u000"],
        "min_visible_fraction": min(fractions.values()),
        "max_visible_fraction_non_admin": max(
            f for u, f in fractions.items() if int(u[1:]) >= N_ADMINS
        ),
    }


def check(stats):
    """Return a list of failures. Empty means the corpus is hard enough."""
    problems = []
    if stats["share_users_with_conflict_depth3"] < 0.30:
        problems.append("fewer than 30% of users have an allow/deny conflict at depth 3 or deeper")
    if stats["share_users_reaching_cycle"] < 0.10:
        problems.append("fewer than 10% of users reach a group cycle")
    if stats["share_users_beyond_depth_bound"] < 0.10:
        problems.append("fewer than 10% of users have membership beyond the depth bound")
    if stats["admin_visible_fraction"] < 1.0:
        problems.append("the administrator does not see every document")
    bands = stats["users_per_visibility_band"]
    if bands["up_to_2_percent"] < 10:
        problems.append("fewer than 10 users in the 1-2% visibility band")
    if bands["10_to_25_percent"] < 30:
        problems.append("fewer than 30 users in the 10-25% visibility band")
    return problems


def pick_personas(built, per_user):
    n_docs = len(built["documents"])

    def first(low, high):
        for u in sorted(per_user):
            if low <= len(per_user[u]["visible"]) / n_docs <= high:
                return u
        return None

    picks = {
        "administrator": "u000",
        "new_joiner": first(0.005, 0.02),
        "junior": first(0.03, 0.09),
        "mid_visibility": first(0.10, 0.25),
    }
    return {
        name: {"user": u, "visible_documents": len(per_user[u]["visible"]),
               "visible_fraction": round(len(per_user[u]["visible"]) / n_docs, 4)}
        for name, u in picks.items() if u is not None
    }


def write_outputs(out, built, per_user, stats, personas, seed):
    out.mkdir(parents=True, exist_ok=True)
    org = {**built["graph"], "groups_meta": built["groups_meta"]}
    (out / "org.json").write_text(json.dumps(org, indent=1))
    with (out / "documents.jsonl").open("w") as fh:
        for d in built["documents"]:
            fh.write(json.dumps(d) + "\n")
    (out / "secrets.json").write_text(json.dumps(built["secrets"], indent=1))
    (out / "personas.json").write_text(json.dumps(personas, indent=1))
    digest = hashlib.sha256(json.dumps(org, sort_keys=True).encode()).hexdigest()
    summary = {"seed": seed, "org_sha256": digest, **stats}
    (out / "stats.json").write_text(json.dumps(summary, indent=1))
    return digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="data/org")
    args = parser.parse_args()

    built = build(args.seed)
    per_user = analyse(built)
    stats = summarise(built, per_user)
    problems = check(stats)
    print(json.dumps(stats, indent=2))
    if problems:
        raise SystemExit("corpus too easy or malformed:\n  " + "\n  ".join(problems))
    personas = pick_personas(built, per_user)
    write_outputs(Path(args.out), built, per_user, stats, personas, args.seed)

    from provenance import record_source

    record_source(
        "synthetic_org",
        "MIT (generated by scripts/gen_org.py, ships with the repository)",
        "LICENSE in this repository",
        seed=args.seed,
        principals=stats["principals"],
        groups=stats["groups"],
        documents=stats["documents"],
    )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()