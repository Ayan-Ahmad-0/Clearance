import hashlib
import json

import gen_org

PINNED = "e25a1abe5c2658b2073900c5c5844f18c425711451e145adbb8cefa7cc30182f"  # org_sha256 from stats.json on the machine that produced the benchmarks


def test_seed_42_snapshot_is_pinned():
    built = gen_org.build(42)
    org = {**built["graph"], "groups_meta": built["groups_meta"]}
    digest = hashlib.sha256(json.dumps(org, sort_keys=True).encode()).hexdigest()
    assert digest == PINNED