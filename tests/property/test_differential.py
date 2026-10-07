"""Differential property tests: the SQL resolver against the reference resolver.

Random organisations up to 30 principals and 50 documents, with cycles, long
chains (membership beyond the depth bound), deny rows, and rows with unknown ids.
Every example runs inside a savepoint that is rolled back.

  pytest tests/property -rs --hypothesis-show-statistics
  set HYPOTHESIS_EXAMPLES=1000     (deeper run)
"""
import copy
import os
from itertools import pairwise

import psycopg
import pytest
from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st

from clearance.access.loader import replace_snapshot
from reference_resolver import authorised

DSN = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")

EXAMPLES = int(os.environ.get("HYPOTHESIS_EXAMPLES", "300"))
SETTINGS = settings(
    max_examples=EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


@pytest.fixture(scope="module")
def db():
    c = psycopg.connect(DSN)
    c.execute("SELECT 1")  # open the outer transaction; examples nest as savepoints
    yield c
    c.rollback()
    c.close()


@st.composite
def snapshots(draw, allow_unknown=True):
    users = [f"u{i}" for i in range(draw(st.integers(1, 8)))]
    groups = [f"G{i}" for i in range(draw(st.integers(1, 22)))]
    folders = [f"F{i}" for i in range(draw(st.integers(1, 10)))]
    docs = [f"d{i}" for i in range(draw(st.integers(1, 50)))]

    # Folder parents come from earlier folders only, so the tree is acyclic.
    node_parent = {}
    for i, f in enumerate(folders):
        node_parent[f] = draw(st.sampled_from([None, *folders[:i]]))
    for d in docs:
        node_parent[d] = draw(st.sampled_from([None, *folders]))

    # Random membership edges (cycles and self-loops allowed) plus one explicit
    # chain from the first user, so depths past the bound actually occur.
    edges = draw(
        st.lists(
            st.tuples(st.sampled_from(users + groups), st.sampled_from(groups)), max_size=40
        )
    )
    chain_len = draw(st.integers(0, min(len(groups), 7)))
    order = draw(st.permutations(groups))
    path = [users[0], *order[:chain_len]]
    edges = [*edges, *pairwise(path)]

    node = st.one_of(st.sampled_from(folders), st.sampled_from(docs))
    subject = st.one_of(st.sampled_from(groups), st.sampled_from(groups), st.sampled_from(users))
    effect = st.sampled_from(["allow", "allow", "deny"])
    rows = draw(st.lists(st.tuples(node, subject, effect), max_size=40))

    if allow_unknown:
        node_parent["d900"] = "F99"  # unknown parent: acts as a root
        edges = [*edges, ("u99", groups[0]), (users[0], "G99")]
        rows = [
            *rows,
            ("d900", groups[0], "allow"),
            ("d99", groups[0], "allow"),
            (docs[0], "G99", "deny"),
        ]

    return {
        "users": users,
        "groups": groups,
        "member_of": [list(e) for e in edges],
        "node_parent": node_parent,
        "acl": [list(r) for r in rows],
    }


def sql_authorised(db, user):
    return {r[0] for r in db.execute("SELECT doc_id FROM authorised_documents(%s)", (user,))}


@SETTINGS
@given(graph=snapshots())
def test_sql_agrees_with_reference_on_random_organisations(db, graph):
    with db.transaction(force_rollback=True):
        replace_snapshot(db, graph)
        docs = sorted(n for n in graph["node_parent"] if n.startswith("d"))
        deepest = db.execute("SELECT coalesce(max(depth), 0) FROM membership_closure").fetchone()[0]
        event("membership beyond bound" if deepest > 4 else "membership within bound")
        anyone_sees = False
        for u in graph["users"]:
            expected = authorised(graph, u)
            anyone_sees = anyone_sees or bool(expected)
            assert sql_authorised(db, u) == expected, u
            for d in docs[::7]:
                got = db.execute("SELECT can_read(%s, %s)", (u, d)).fetchone()[0]
                assert got == (d in expected), (u, d)
        event("some user sees documents" if anyone_sees else "nobody sees anything")


OPS = ("add_edge", "remove_edge", "add_acl", "remove_acl", "move_node")
OP = st.tuples(
    st.sampled_from(OPS),
    st.integers(0, 10**6),
    st.integers(0, 10**6),
    st.sampled_from(["allow", "deny"]),
)


def refresh_below(db, child):
    db.execute("SELECT refresh_membership_closure(users_below(%s::text[]))", ([child],))


def apply_op(db, graph, op):
    """Apply one edit to the database (incremental refresh) and to the python snapshot."""
    kind, i, j, effect = op
    principals = graph["users"] + graph["groups"]
    nodes = sorted(graph["node_parent"])

    if kind == "add_edge":
        child, parent = principals[i % len(principals)], graph["groups"][j % len(graph["groups"])]
        db.execute(
            "INSERT INTO membership (child, parent) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (child, parent),
        )
        if [child, parent] not in graph["member_of"]:
            graph["member_of"].append([child, parent])
        refresh_below(db, child)

    elif kind == "remove_edge":
        edges = graph["member_of"]
        if not edges:
            return
        child, parent = edges[i % len(edges)]
        db.execute("DELETE FROM membership WHERE child = %s AND parent = %s", (child, parent))
        graph["member_of"] = [e for e in edges if e != [child, parent]]
        refresh_below(db, child)

    elif kind == "add_acl":
        row = [nodes[i % len(nodes)], principals[j % len(principals)], effect]
        db.execute(
            "INSERT INTO acl (node_id, subject_id, effect) VALUES (%s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            row,
        )
        if row not in graph["acl"]:
            graph["acl"].append(row)

    elif kind == "remove_acl":
        rows = graph["acl"]
        if not rows:
            return
        row = rows[i % len(rows)]
        db.execute(
            "DELETE FROM acl WHERE node_id = %s AND subject_id = %s AND effect = %s", row
        )
        graph["acl"] = [r for r in rows if r != row]

    else:  # move_node
        node = nodes[i % len(nodes)]
        options = [None, *[n for n in nodes if n.startswith("F")]]
        new_parent = options[j % len(options)]
        try:
            with db.transaction():
                db.execute("UPDATE nodes SET parent_id = %s WHERE id = %s", (new_parent, node))
        except psycopg.errors.CheckViolation:
            return  # would make a cycle (or a self-parent); the database refused it
        graph["node_parent"][node] = new_parent
        db.execute("SELECT refresh_node_ancestors(%s::text[])", ([node],))


@settings(
    max_examples=max(EXAMPLES // 2, 100),
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@given(graph=snapshots(allow_unknown=False), ops=st.lists(OP, max_size=8))
def test_incremental_refresh_equals_scratch_and_reference(db, graph, ops):
    graph = copy.deepcopy(graph)
    with db.transaction(force_rollback=True):
        replace_snapshot(db, graph)
        for op in ops:
            apply_op(db, graph, op)
            for u in graph["users"]:
                assert sql_authorised(db, u) == authorised(graph, u), (op, u)

        closure = set(db.execute("SELECT user_id, group_id, depth FROM membership_closure"))
        ancestors = set(db.execute("SELECT node_id, ancestor_id FROM node_ancestors"))
        db.execute(
            "SELECT refresh_membership_closure(ARRAY(SELECT id FROM principals WHERE kind = 'user'))"
        )
        db.execute(
            "SELECT refresh_node_ancestors(ARRAY(SELECT id FROM nodes WHERE parent_id IS NULL))"
        )
        assert closure == set(
            db.execute("SELECT user_id, group_id, depth FROM membership_closure")
        )
        assert ancestors == set(db.execute("SELECT node_id, ancestor_id FROM node_ancestors"))