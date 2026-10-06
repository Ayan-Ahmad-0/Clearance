"""Reference resolver, written from docs/resolver_spec.md only.

Rules for this module:
  * imports nothing from `clearance` (enforced by import-linter in CI)
  * slow and simple on purpose: recomputes from the raw snapshot every call
  * no caching, no SQL, no shared helpers with the fast path
"""
from collections import deque

DEPTH_BOUND = 4  # spec section 4
ALLOW = "ALLOW"
DENY = "DENY"


class InvalidSnapshot(ValueError):
    """The snapshot breaks a structural rule (spec section 3)."""


def _check_and_index(graph):
    users = set(graph["users"])
    groups = set(graph["groups"])
    node_parent = graph["node_parent"]

    if users & groups:
        raise InvalidSnapshot(f"ids not unique across users and groups: {users & groups}")

    # The folder tree must not contain a cycle (spec section 3).
    for start in node_parent:
        seen = set()
        cur = start
        while cur is not None:
            if cur in seen:
                raise InvalidSnapshot(f"cycle in node_parent at {cur}")
            seen.add(cur)
            cur = node_parent.get(cur)

    # child -> list of parent groups. Rows with unknown ids are ignored.
    edges = {}
    for child, parent in graph["member_of"]:
        if parent in groups and (child in users or child in groups):
            edges.setdefault(child, []).append(parent)

    # node -> list of (subject, effect)
    rows = {}
    for node, subject, effect in graph["acl"]:
        if effect not in (ALLOW.lower(), DENY.lower()):
            raise InvalidSnapshot(f"bad effect: {effect!r}")
        if node in node_parent and (subject in users or subject in groups):
            rows.setdefault(node, []).append((subject, effect))

    return users, groups, edges, rows


def _group_depths(edges, user):
    """Shortest membership path length from `user` to every reachable group (BFS)."""
    depths = {}
    seen = {user}
    queue = deque([(user, 0)])
    while queue:
        node, d = queue.popleft()
        for parent in edges.get(node, []):
            if parent not in seen:
                seen.add(parent)
                depths[parent] = d + 1
                queue.append((parent, d + 1))
    return depths


def _ancestors_inclusive(node_parent, node):
    chain = []
    cur = node
    while cur is not None:
        chain.append(cur)
        cur = node_parent.get(cur)
    return chain


class _Prepared:
    def __init__(self, graph, user):
        users, _groups, edges, rows = _check_and_index(graph)
        if user not in users:
            raise ValueError(f"unknown user {user!r}")
        self.user = user
        self.node_parent = graph["node_parent"]
        self.rows = rows
        self.depths = _group_depths(edges, user)


def _decide(ctx, doc):
    allow_hit = False
    for node in _ancestors_inclusive(ctx.node_parent, doc):
        for subject, effect in ctx.rows.get(node, []):
            if subject == ctx.user:
                depth = 0
            elif subject in ctx.depths:
                depth = ctx.depths[subject]
            else:
                continue  # row does not apply to this user
            if effect == "deny":
                return DENY  # deny wins at any depth (spec section 6, rule 2)
            if depth <= DEPTH_BOUND:
                allow_hit = True  # allows are bounded (rule 3)
    return ALLOW if allow_hit else DENY  # default deny (rule 4)


def decide(graph, user, doc):
    """Return "ALLOW" or "DENY"."""
    return _decide(_Prepared(graph, user), doc)


def can_read(graph, user, doc):
    return decide(graph, user, doc) == ALLOW


def documents(graph):
    """Documents are leaves with ids in the d* namespace (spec section 2)."""
    return sorted(n for n in graph["node_parent"] if n.startswith("d"))


def authorised(graph, user):
    """All documents the user may read (spec section 8)."""
    ctx = _Prepared(graph, user)
    return {d for d in documents(graph) if _decide(ctx, d) == ALLOW}


def reaches_cycle(graph, user):
    """True if a group cycle is reachable from the user (spec section 7).
    Informational only; it never changes a decision."""
    _users, _groups, edges, _rows = _check_and_index(graph)
    state = {}  # group -> "open" | "done"

    def visit(node):
        for parent in edges.get(node, []):
            if state.get(parent) == "open":
                return True
            if parent not in state:
                state[parent] = "open"
                if visit(parent):
                    return True
                state[parent] = "done"
        return False

    return visit(user)