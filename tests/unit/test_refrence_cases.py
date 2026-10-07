"""The named cases from docs/resolver_spec.md section 11, run against the reference."""
import random
from itertools import pairwise

import pytest

from reference_resolver import ALLOW, DENY, authorised, decide, reaches_cycle


def make(edges=(), acl=(), parents=None, users=("u1",)):
    """Build a snapshot. Groups are inferred: any id in an edge or ACL subject
    that is not a user. Nodes default to having no parent."""
    users = set(users)
    ids = {x for e in edges for x in e} | {s for _, s, _ in acl}
    groups = sorted(i for i in ids if i not in users)
    node_parent = dict(parents or {})
    for node, _, _ in acl:
        node_parent.setdefault(node, None)
    return {
        "users": sorted(users),
        "groups": groups,
        "member_of": list(edges),
        "node_parent": node_parent,
        "acl": list(acl),
    }


def chain(*ids):
    return list(pairwise(ids))


CASES = {
    "01_default_deny": (make(parents={"d1": None}), "d1", DENY),
    "02_direct_user_allow": (make(acl=[("d1", "u1", "allow")]), "d1", ALLOW),
    "03_group_allow": (make([("u1", "G1")], [("d1", "G1", "allow")]), "d1", ALLOW),
    "04_nested_depth_2": (
        make(chain("u1", "G1", "G2"), [("d1", "G2", "allow")]), "d1", ALLOW),
    "05_depth_exactly_bound": (
        make(chain("u1", "G1", "G2", "G3", "G4"), [("d1", "G4", "allow")]), "d1", ALLOW),
    "06_depth_past_bound": (
        make(chain("u1", "G1", "G2", "G3", "G4", "G5"), [("d1", "G5", "allow")]), "d1", DENY),
    "07_deny_beats_allow_same_node": (
        make([("u1", "G1")], [("d1", "G1", "allow"), ("d1", "u1", "deny")]), "d1", DENY),
    "08_deny_via_nested_group": (
        make([("u1", "G1")] + chain("u1", "G2", "G3"),
             [("d1", "G1", "allow"), ("d1", "G3", "deny")]), "d1", DENY),
    "09_folder_deny_beats_doc_allow": (
        make([("u1", "G1")], [("F1", "G1", "deny"), ("d1", "G1", "allow")],
             parents={"d1": "F1", "F1": None}), "d1", DENY),
    "10_folder_allow_inherited": (
        make([("u1", "G1")], [("F1", "G1", "allow")],
             parents={"d1": "F1", "F1": None}), "d1", ALLOW),
    "11_nested_folder_inherited": (
        make([("u1", "G1")], [("F1", "G1", "allow")],
             parents={"d1": "F2", "F2": "F1", "F1": None}), "d1", ALLOW),
    "12_deny_past_bound_still_applies": (
        make([("u1", "G1")] + chain("u1", "H1", "H2", "H3", "H4", "H5"),
             [("d1", "G1", "allow"), ("d1", "H5", "deny")]), "d1", DENY),
    "13_cycle_reaches_group": (
        make([("u1", "G1"), ("G1", "G2"), ("G2", "G1")],
             [("d1", "G2", "allow")]), "d1", ALLOW),
    "14_cycle_no_membership_no_grant": (
        make([("G1", "G2"), ("G2", "G1")], [("d1", "G1", "allow")]), "d1", DENY),
    "15_deny_inside_cycle": (
        make([("u1", "G1"), ("G1", "G2"), ("G2", "G1")],
             [("d1", "G1", "allow"), ("d1", "G2", "deny")]), "d1", DENY),
    "16_unrelated_deny_ignored": (
        make([("u1", "G1")], [("d1", "G1", "allow"), ("d1", "G9", "deny")]), "d1", ALLOW),
    "18_shortest_path_counts": (
        make(chain("u1", "G1", "G2", "G3", "G4", "G5") + [("u1", "G5")],
             [("d1", "G5", "allow")]), "d1", ALLOW),
    "19_same_node_conflict": (
        make([("u1", "G1")], [("d1", "G1", "allow"), ("d1", "G1", "deny")]), "d1", DENY),
    "21_two_allow_paths_one_deny": (
        make([("u1", "G1"), ("u1", "G2"), ("u1", "G3")],
             [("d1", "G1", "allow"), ("d1", "G2", "allow"), ("d1", "G3", "deny")]),
        "d1", DENY),
    "22_user_deny_beats_group_folder_allow": (
        make([("u1", "G1")], [("F1", "G1", "allow"), ("d1", "u1", "deny")],
             parents={"d1": "F1", "F1": None}),
        "d1", DENY),
    "23_over_bound_chain_with_cycle": (
        make(chain("u1", "G1", "G2", "G3", "G4", "G5") + [("G5", "G3")],
             [("d1", "G5", "allow")]),
        "d1", DENY),
    "24_deny_in_middle_of_deep_folders": (
        make([("u1", "G1")], [("R", "G1", "allow"), ("F2", "G1", "deny")],
             parents={"d1": "F3", "F3": "F2", "F2": "F1", "F1": "R", "R": None}),
        "d1", DENY),
    "25_empty_graph": (make(), "d1", DENY),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_named_case(name):
    graph, doc, expected = CASES[name]
    assert decide(graph, "u1", doc) == expected


def test_17_rows_are_per_document():
    g = make([("u1", "G1")], [("d1", "G1", "allow"), ("d2", "G1", "deny")])
    assert decide(g, "u1", "d1") == ALLOW
    assert decide(g, "u1", "d2") == DENY


def test_20_deny_scoped_to_subtree():
    g = make(
        [("u1", "G1")],
        [("R", "G1", "allow"), ("F1", "G1", "deny")],
        parents={"d1": "F1", "F1": "R", "d2": "F2", "F2": "R", "R": None},
    )
    assert decide(g, "u1", "d1") == DENY
    assert decide(g, "u1", "d2") == ALLOW
    assert authorised(g, "u1") == {"d2"}


def test_cycle_flag_is_informational():
    with_cycle = CASES["13_cycle_reaches_group"][0]
    without = CASES["03_group_allow"][0]
    assert reaches_cycle(with_cycle, "u1") is True
    assert reaches_cycle(without, "u1") is False


def test_row_order_is_irrelevant():
    graph, doc, expected = CASES["08_deny_via_nested_group"]
    rng = random.Random(0)
    for _ in range(20):
        shuffled = dict(graph)
        shuffled["member_of"] = rng.sample(graph["member_of"], len(graph["member_of"]))
        shuffled["acl"] = rng.sample(graph["acl"], len(graph["acl"]))
        assert decide(shuffled, "u1", doc) == expected


def test_folder_tree_cycle_is_rejected():
    from reference_resolver import InvalidSnapshot
    g = make(acl=[("d1", "u1", "allow")], parents={"d1": "F1", "F1": "F2", "F2": "F1"})
    with pytest.raises(InvalidSnapshot):
        decide(g, "u1", "d1")


def test_25_empty_graph_authorises_nothing():
    assert authorised(make(), "u1") == set()