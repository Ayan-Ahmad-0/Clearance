"""Checks on the synthetic organisation: determinism, hardness, leak strings, and
agreement between the generator's own instrumentation and the reference resolver."""
import gen_org
import pytest

from reference_resolver import authorised


@pytest.fixture(scope="module")
def built():
    return gen_org.build(42)


@pytest.fixture(scope="module")
def per_user(built):
    return gen_org.analyse(built)


def test_sizes_match_the_brief(built):
    graph = built["graph"]
    assert len(graph["users"]) == 400
    assert len(graph["groups"]) == 60
    assert len(built["documents"]) == 1800
    assert {d["tier"] for d in built["documents"]} == set(gen_org.TIERS)


def test_same_seed_same_organisation():
    a = gen_org.build(7)
    b = gen_org.build(7)
    assert a["graph"] == b["graph"]
    assert a["documents"] == b["documents"]
    assert a["secrets"] == b["secrets"]
    assert gen_org.build(8)["graph"] != a["graph"]


def test_corpus_is_hard_enough(built, per_user):
    stats = gen_org.summarise(built, per_user)
    assert gen_org.check(stats) == []


def test_secrets_live_only_in_restricted_documents(built):
    tier = {d["id"]: d["tier"] for d in built["documents"]}
    text = {d["id"]: d["text"] for d in built["documents"]}
    assert built["secrets"], "no secrets generated"
    for doc_id, strings in built["secrets"].items():
        assert tier[doc_id] == "restricted"
        for s in strings:
            assert s in text[doc_id]
    all_secrets = [s for v in built["secrets"].values() for s in v]
    assert len(all_secrets) == len(set(all_secrets)), "secret strings must be unique"
    for d in built["documents"]:
        if d["tier"] != "restricted":
            assert not any(s in d["text"] for s in all_secrets)


def test_ids_follow_the_spec_namespaces(built):
    graph = built["graph"]
    assert all(u.startswith("u") for u in graph["users"])
    assert all(g.startswith("G") for g in graph["groups"])
    for node in graph["node_parent"]:
        assert node[0] in ("F", "d")


def test_instrumentation_agrees_with_reference_resolver(built, per_user):
    # Spread of users: admins, new joiners, and a sample of regular users.
    sample = ["u000", "u001", "u003", "u010", "u027", "u024", *built["graph"]["users"][50:400:25]]
    for u in sample:
        assert set(per_user[u]["visible"]) == authorised(built["graph"], u), u


def test_out_of_bound_allow_never_grants(built):
    """Chain groups hang under two departments. An allow on the last chain group is
    deeper than the bound, so no user may gain access through it alone."""
    graph = built["graph"]
    chain_last = next(g for g, m in built["groups_meta"].items() if m["name"] == "chain-4")
    rows = [r for r in graph["acl"] if r[1] == chain_last and r[2] == "allow"]
    assert rows, "expected out-of-bound allow rows"
    folder_docs = {r[0]: {d["id"] for d in built["documents"] if d["folder"] == r[0]} for r in rows}
    for u in graph["users"][3:]:
        got = authorised(graph, u)
        stripped = {**graph, "acl": [r for r in graph["acl"] if r not in rows]}
        assert got == authorised(stripped, u), u
        assert folder_docs  # rows point at real folders