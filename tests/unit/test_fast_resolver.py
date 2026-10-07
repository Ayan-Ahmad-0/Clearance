"""The SQL resolver against the named cases and against the reference resolver."""
import os

import gen_org
import psycopg
import pytest
from test_refrence_cases import CASES, make

from clearance.access.loader import replace_snapshot
from reference_resolver import ALLOW, DENY, authorised

DSN = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")


@pytest.fixture
def conn():
    c = psycopg.connect(DSN)
    yield c
    c.rollback()
    c.close()


def sql_authorised(conn, user):
    return {r[0] for r in conn.execute("SELECT doc_id FROM authorised_documents(%s)", (user,))}


def sql_decide(conn, user, node):
    ok = conn.execute("SELECT can_read(%s, %s)", (user, node)).fetchone()[0]
    return ALLOW if ok else DENY


@pytest.mark.parametrize("name", sorted(CASES))
def test_named_case_in_sql(conn, name):
    graph, doc, expected = CASES[name]
    replace_snapshot(conn, graph)
    assert sql_decide(conn, "u1", doc) == expected


def test_17_rows_are_per_document_in_sql(conn):
    g = make([("u1", "G1")], [("d1", "G1", "allow"), ("d2", "G1", "deny")])
    replace_snapshot(conn, g)
    assert sql_decide(conn, "u1", "d1") == ALLOW
    assert sql_decide(conn, "u1", "d2") == DENY
    assert sql_authorised(conn, "u1") == {"d1"}


def test_20_deny_scoped_to_subtree_in_sql(conn):
    g = make(
        [("u1", "G1")],
        [("FR", "G1", "allow"), ("F1", "G1", "deny")],
        parents={"d1": "F1", "F1": "FR", "d2": "F2", "F2": "FR", "FR": None},
    )
    replace_snapshot(conn, g)
    assert sql_authorised(conn, "u1") == {"d2"}


def test_folders_are_never_in_the_authorised_set(conn):
    g = make([("u1", "G1")], [("F1", "G1", "allow")], parents={"d1": "F1", "F1": None})
    replace_snapshot(conn, g)
    assert sql_authorised(conn, "u1") == {"d1"}


def test_unknown_user_raises(conn):
    replace_snapshot(conn, make(acl=[("d1", "u1", "allow")]))
    with pytest.raises(psycopg.Error), conn.transaction():
        conn.execute("SELECT * FROM authorised_documents('u404')")


@pytest.fixture(scope="module")
def org():
    return gen_org.build(42)["graph"]


def test_generated_org_matches_reference_on_a_sample(conn, org):
    replace_snapshot(conn, org)
    sample = org["users"][::4]  # 100 users; the full 400 x 1,800 run is a separate job
    for u in sample:
        assert sql_authorised(conn, u) == authorised(org, u), u


def test_point_query_agrees_with_set_query(conn, org):
    replace_snapshot(conn, org)
    docs = [n for n in org["node_parent"] if n.startswith("d")]
    for u in ("u000", "u003", "u010", "u027", "u200"):
        allowed = sql_authorised(conn, u)
        for d in docs[::37]:
            assert (sql_decide(conn, u, d) == ALLOW) == (d in allowed), (u, d)