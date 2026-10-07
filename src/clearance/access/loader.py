"""Load a resolver snapshot (the org.json graph) into the entitlement tables.

Rules come from docs/resolver_spec.md section 3:
  * rows that mention unknown ids are ignored (counted in the report)
  * a cycle in the folder tree is an invalid snapshot
  * ids must follow the namespaces: u* users, G* groups, F* folders, d* documents

All checking happens before the transaction opens, so a bad snapshot leaves the
previously loaded organisation untouched.
"""
from dataclasses import dataclass, field

EFFECTS = ("allow", "deny")


class SnapshotError(ValueError):
    """The snapshot breaks a structural rule."""


@dataclass
class LoadReport:
    users: int = 0
    groups: int = 0
    folders: int = 0
    documents: int = 0
    membership: int = 0
    acl: int = 0
    closure_rows: int = 0
    ancestor_rows: int = 0
    ignored: dict = field(default_factory=lambda: {"member_of": 0, "acl": 0, "node_parent": 0})


@dataclass
class _Plan:
    principals: list
    nodes: list  # (id, kind, parent_id), parents before children
    membership: list
    acl: list
    users: list
    roots: list
    ignored: dict


def _depth_order(parent):
    """Order nodes so every parent comes before its children. Rejects cycles."""
    depth = {}
    for start in parent:
        path, on_path, cur = [], set(), start
        while cur is not None and cur not in depth:
            if cur in on_path:
                raise SnapshotError(f"cycle in node_parent at {cur}")
            on_path.add(cur)
            path.append(cur)
            cur = parent[cur]
        base = -1 if cur is None else depth[cur]
        for node in reversed(path):
            base += 1
            depth[node] = base
    return sorted(parent, key=lambda n: (depth[n], n))


def plan(graph):
    users = list(graph["users"])
    groups = list(graph["groups"])
    uset, gset = set(users), set(groups)

    if uset & gset:
        raise SnapshotError(f"ids not unique across users and groups: {sorted(uset & gset)}")
    for u in users:
        if not u.startswith("u"):
            raise SnapshotError(f"user id must start with u: {u!r}")
    for g in groups:
        if not g.startswith("G"):
            raise SnapshotError(f"group id must start with G: {g!r}")

    raw_parent = graph["node_parent"]
    node_ids = set(raw_parent)
    ignored = {"member_of": 0, "acl": 0, "node_parent": 0}

    parent = {}
    for node, par in raw_parent.items():
        if node[:1] not in ("d", "F"):
            raise SnapshotError(f"node id must start with d or F: {node!r}")
        if par is None:
            parent[node] = None
        elif par not in node_ids:
            parent[node] = None  # unknown parent: no rows can exist on it, so it acts as a root
            ignored["node_parent"] += 1
        elif par.startswith("d"):
            raise SnapshotError(f"document {par} used as the parent of {node}")
        else:
            parent[node] = par
    ordered = _depth_order(parent)

    members = set()
    for child, par in graph["member_of"]:
        if par in gset and (child in uset or child in gset):
            members.add((child, par))
        else:
            ignored["member_of"] += 1

    subjects = uset | gset
    acl = set()
    for node, subject, effect in graph["acl"]:
        if effect not in EFFECTS:
            raise SnapshotError(f"bad effect: {effect!r}")
        if node in node_ids and subject in subjects:
            acl.add((node, subject, effect))
        else:
            ignored["acl"] += 1

    return _Plan(
        principals=[(u, "user") for u in users] + [(g, "group") for g in groups],
        nodes=[(n, "document" if n.startswith("d") else "folder", parent[n]) for n in ordered],
        membership=sorted(members),
        acl=sorted(acl),
        users=users,
        roots=[n for n in ordered if parent[n] is None],
        ignored=ignored,
    )


def replace_snapshot(conn, graph):
    """Replace the loaded organisation with `graph`. Returns a LoadReport."""
    p = plan(graph)
    with conn.transaction():
        conn.execute("TRUNCATE acl, membership, nodes, principals CASCADE")
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO principals (id, kind) VALUES (%s, %s)", p.principals)
            cur.executemany("INSERT INTO nodes (id, kind, parent_id) VALUES (%s, %s, %s)", p.nodes)
            cur.executemany("INSERT INTO membership (child, parent) VALUES (%s, %s)", p.membership)
            cur.executemany(
                "INSERT INTO acl (node_id, subject_id, effect) VALUES (%s, %s, %s)", p.acl
            )
        conn.execute("SELECT refresh_membership_closure(%s::text[])", (p.users,))
        conn.execute("SELECT refresh_node_ancestors(%s::text[])", (p.roots,))
        closure_rows = conn.execute("SELECT count(*) FROM membership_closure").fetchone()[0]
        ancestor_rows = conn.execute("SELECT count(*) FROM node_ancestors").fetchone()[0]

    return LoadReport(
        users=len(p.users),
        groups=len(p.principals) - len(p.users),
        folders=sum(1 for _, kind, _ in p.nodes if kind == "folder"),
        documents=sum(1 for _, kind, _ in p.nodes if kind == "document"),
        membership=len(p.membership),
        acl=len(p.acl),
        closure_rows=closure_rows,
        ancestor_rows=ancestor_rows,
        ignored=p.ignored,
    )