"""Loader tests: filtering, hard errors, and the closures on the generated organisation."""
import os

import gen_org
import psycopg
import pytest

from clearance.access.loader import SnapshotError, replace_snapshot

DSN = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")


@pytest.fixture
def conn():
    c = psycopg.connect(DSN)
    yield c
    c.rollback()
    c.close()


@pytest.fixture(scope="module")
def org():
    return gen_org.build(42)["graph"]


def small(**override):
    graph = {
        "users": ["u1"],
        "groups": ["G1"],
        "member_of": [["u1", "G1"]],
        "node_parent": {"F1": None, "d1": "F1"},
        "acl": [["d1", "G1", "allow"]],
    }
    graph.update(override)
    return graph


def count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def level_by_level_depths(member_of, user):
    edges = {}
    for child, parent in member_of:
        edges.setdefault(child, []).append(parent)
    depths, seen, frontier, d = {}, {user}, [user], 0
    while frontier:
        d += 1
        nxt = []
        for node in frontier:
            for parent in edges.get(node, []):
                if parent not in seen:
                    seen.add(parent)
                    depths[parent] = d
                    nxt.append(parent)
        frontier = nxt
    return depths


def test_generated_org_loads_with_nothing_ignored(conn, org):
    r = replace_snapshot(conn, org)
    assert (r.users, r.groups, r.documents) == (400, 60, 1800)
    assert r.folders == 62
    assert r.membership == len({tuple(e) for e in org["member_of"]})
    assert r.acl == len({tuple(row) for row in org["acl"]})
    assert r.ignored == {"member_of": 0, "acl": 0, "node_parent": 0}


def test_membership_closure_matches_independent_bfs(conn, org):
    replace_snapshot(conn, org)
    expected = {}
    for u in org["users"]:
        for g, d in level_by_level_depths(org["member_of"], u).items():
            expected[(u, g)] = d
    got = {
        (u, g): d
        for u, g, d in conn.execute("SELECT user_id, group_id, depth FROM membership_closure")
    }
    assert got == expected
    assert max(got.values()) > 4, "depths past the bound must be stored (denies need them)"


def test_node_ancestors_match_the_folder_chains(conn, org):
    replace_snapshot(conn, org)
    parent = org["node_parent"]
    expected = set()
    for node in parent:
        cur = node
        while cur is not None:
            expected.add((node, cur))
            cur = parent.get(cur)
    got = set(conn.execute("SELECT node_id, ancestor_id FROM node_ancestors").fetchall())
    assert got == expected


def test_unknown_ids_are_ignored_and_counted(conn):
    graph = small(
        member_of=[["u1", "G1"], ["u1", "G9"], ["u9", "G1"]],
        node_parent={"F1": None, "d1": "F1", "d2": "F7"},
        acl=[["d1", "G1", "allow"], ["d1", "G9", "allow"], ["d5", "G1", "allow"]],
    )
    r = replace_snapshot(conn, graph)
    assert r.membership == 1 and r.acl == 1
    assert r.ignored == {"member_of": 2, "acl": 2, "node_parent": 1}
    assert count(conn, "nodes") == 3


@pytest.mark.parametrize(
    "bad",
    [
        small(node_parent={"F1": "F2", "F2": "F1"}, acl=[]),
        small(node_parent={"d1": None, "d2": "d1"}, acl=[]),
        small(acl=[["d1", "G1", "maybe"]]),
        small(users=["u1"], groups=["u1"], member_of=[], acl=[]),
        small(node_parent={"x1": None}, acl=[]),
    ],
    ids=["folder_cycle", "document_as_parent", "bad_effect", "id_in_both", "bad_namespace"],
)
def test_structural_violations_raise(conn, bad):
    with pytest.raises(SnapshotError):
        replace_snapshot(conn, bad)


def test_a_failed_load_leaves_the_previous_organisation(conn):
    replace_snapshot(conn, small())
    before = (count(conn, "principals"), count(conn, "nodes"), count(conn, "acl"))
    with pytest.raises(SnapshotError):
        replace_snapshot(conn, small(node_parent={"F1": "F2", "F2": "F1"}))
    assert (count(conn, "principals"), count(conn, "nodes"), count(conn, "acl")) == before


def test_loading_twice_gives_the_same_result(conn):
    first = replace_snapshot(conn, small())
    second = replace_snapshot(conn, small())
    assert first == second