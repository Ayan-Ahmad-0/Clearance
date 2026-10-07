"""DB-level checks for the entitlement schema and its two closures."""
import os

import psycopg
import pytest

DSN = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")


@pytest.fixture
def conn():
    c = psycopg.connect(DSN)
    yield c
    c.rollback()
    c.close()


def add_principals(conn, users=(), groups=()):
    for u in users:
        conn.execute("INSERT INTO principals (id, kind) VALUES (%s, 'user')", (u,))
    for g in groups:
        conn.execute("INSERT INTO principals (id, kind) VALUES (%s, 'group')", (g,))


def add_edges(conn, edges):
    for child, parent in edges:
        conn.execute("INSERT INTO membership (child, parent) VALUES (%s, %s)", (child, parent))


def add_node(conn, node_id, kind, parent=None):
    conn.execute(
        "INSERT INTO nodes (id, kind, parent_id) VALUES (%s, %s, %s)", (node_id, kind, parent)
    )


def closure(conn, user_id=None):
    if user_id is None:
        rows = conn.execute("SELECT user_id, group_id, depth FROM membership_closure").fetchall()
    else:
        rows = conn.execute(
            "SELECT user_id, group_id, depth FROM membership_closure WHERE user_id = %s",
            (user_id,),
        ).fetchall()
    return {(u, g): d for u, g, d in rows}


def refresh_all(conn):
    conn.execute(
        "SELECT refresh_membership_closure(ARRAY(SELECT id FROM principals WHERE kind = 'user'))"
    )


def rejected(conn, sql, params=()):
    with pytest.raises(psycopg.Error), conn.transaction():
        conn.execute(sql, params)


def test_id_prefix_decides_document_or_folder(conn):
    rejected(conn, "INSERT INTO nodes (id, kind) VALUES ('x1', 'document')")
    rejected(conn, "INSERT INTO nodes (id, kind) VALUES ('d1', 'folder')")


def test_a_document_cannot_be_a_parent(conn):
    add_node(conn, "FT1", "folder")
    add_node(conn, "dT1", "document", "FT1")
    rejected(conn, "INSERT INTO nodes (id, kind, parent_id) VALUES ('dT2', 'document', 'dT1')")


def test_a_user_cannot_be_a_membership_parent(conn):
    add_principals(conn, users=["uT1", "uT2"])
    rejected(conn, "INSERT INTO membership (child, parent) VALUES ('uT1', 'uT2')")


def test_folder_cycle_is_rejected(conn):
    add_node(conn, "FT1", "folder")
    add_node(conn, "FT2", "folder", "FT1")
    rejected(conn, "UPDATE nodes SET parent_id = 'FT2' WHERE id = 'FT1'")


def test_closure_keeps_shortest_depth_and_every_depth_through_a_cycle(conn):
    add_principals(conn, users=["uT1"], groups=[f"GT{i}" for i in range(1, 6)])
    add_edges(conn, [("uT1", "GT1"), ("GT1", "GT2"), ("GT2", "GT3"),
                     ("GT3", "GT4"), ("GT4", "GT5"), ("GT5", "GT2")])
    refresh_all(conn)
    assert closure(conn, "uT1") == {
        ("uT1", "GT1"): 1, ("uT1", "GT2"): 2, ("uT1", "GT3"): 3,
        ("uT1", "GT4"): 4, ("uT1", "GT5"): 5,  # past the bound, kept for denies
    }


def test_shortcut_edge_wins(conn):
    add_principals(conn, users=["uT1"], groups=["GT1", "GT2", "GT3"])
    add_edges(conn, [("uT1", "GT1"), ("GT1", "GT2"), ("GT2", "GT3"), ("uT1", "GT3")])
    refresh_all(conn)
    assert closure(conn)[("uT1", "GT3")] == 1


def test_incremental_refresh_equals_scratch(conn):
    add_principals(conn, users=["uT1", "uT2", "uT3"], groups=["GT0", "GT1", "GT2", "GT3"])
    add_edges(conn, [("uT1", "GT1"), ("GT1", "GT2"), ("uT2", "GT0"), ("uT3", "GT3")])
    refresh_all(conn)

    # add an edge, refresh only the affected users
    add_edges(conn, [("GT0", "GT1")])
    conn.execute("SELECT refresh_membership_closure(users_below(ARRAY['GT0']))")
    incremental = closure(conn)
    refresh_all(conn)
    assert incremental == closure(conn)
    assert incremental[("uT2", "GT2")] == 3

    # remove it again
    conn.execute("DELETE FROM membership WHERE child = 'GT0' AND parent = 'GT1'")
    conn.execute("SELECT refresh_membership_closure(users_below(ARRAY['GT0']))")
    incremental = closure(conn)
    refresh_all(conn)
    assert incremental == closure(conn)
    assert ("uT2", "GT2") not in incremental


def test_moving_a_folder_refreshes_only_its_subtree(conn):
    for f in ("FT1", "FT3"):
        add_node(conn, f, "folder")
    add_node(conn, "FT2", "folder", "FT1")
    add_node(conn, "dT1", "document", "FT2")
    conn.execute("SELECT refresh_node_ancestors(ARRAY['FT1', 'FT3'])")

    def ancestors(node):
        rows = conn.execute(
            "SELECT ancestor_id FROM node_ancestors WHERE node_id = %s", (node,)
        ).fetchall()
        return {r[0] for r in rows}

    assert ancestors("dT1") == {"dT1", "FT2", "FT1"}
    conn.execute("UPDATE nodes SET parent_id = 'FT3' WHERE id = 'FT2'")
    conn.execute("SELECT refresh_node_ancestors(ARRAY['FT2'])")
    assert ancestors("dT1") == {"dT1", "FT2", "FT3"}
    assert ancestors("FT1") == {"FT1"}