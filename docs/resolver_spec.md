# Resolver specification (v1)

Status: normative. The reference resolver and the fast resolver are both written
from this document only. If this document is ambiguous, fix the document first.

## 1. Purpose

Given a snapshot of the access graph, decide whether a principal may read a document.
The resolver is a pure function of the snapshot: same input, same output. It has no
clock, no cache and no side effects.

## 2. Entities

- **User**: a person who asks questions. Id namespace `u*`.
- **Group**: a set of members. Id namespace `G*`.
- **Node**: a folder or a document. A document is a leaf node. Id namespace `F*` for
  folders, `d*` for documents. Every node has at most one parent folder. A root folder
  has no parent.
- Ids are unique across all namespaces.
- A node is a document if and only if its id starts with d. Folders start with F, groups with G, users with u.
## 3. Input snapshot

```python
graph = {
    "users":       ["u1", "u2"],
    "groups":      ["G1", "G2"],
    "member_of":   [("u1", "G1"), ("G1", "G2")],   # (child, parent): child is a direct member of parent
    "node_parent": {"d1": "F1", "F1": None},        # every node appears as a key
    "acl":         [("F1", "G1", "allow")],         # (node, subject, effect)
}
```

- `member_of` child may be a user or a group. Parent must be a group.
- `acl` subject may be a user or a group. Effect is `"allow"` or `"deny"`.
- Edges or rows that mention an id not listed in `users`, `groups` or `node_parent`
  are ignored.
- If `node_parent` contains a cycle, the snapshot is invalid and the resolver raises an
  error. (The group graph may contain cycles; the folder tree may not.)

## 4. Membership and depth

- A **membership path** from user `U` to group `G` is a sequence of `member_of` edges
  `U -> G1 -> G2 -> ... -> G`. Its **length** is the number of edges.
- `U -> G` directly has length 1.
- The **depth** of `U` in `G` is the length of the shortest membership path from `U` to `G`.
- A user is a member of a group if any path exists (at any length). A user is never a
  "member" of a user.
- A subject that is the user itself counts as depth 0 for ACL purposes.
- The **depth bound** is `D = 4`.

## 5. Which ACL rows apply

For a document `d`, the **applicable rows** are all `acl` rows whose node is `d` or any
ancestor folder of `d` (following `node_parent` up to the root).

A row applies to user `U` through its subject `S`:

- If `S == U`, it matches at depth 0.
- If `S` is a group and `U` is a member of `S`, it matches at depth = depth of `U` in `S`.
- Otherwise it does not match.

## 6. Decision

For user `U` and document `d`, the decision is `ALLOW` or `DENY`:

1. Collect the applicable rows (section 5).
2. **Deny rule.** If any applicable `deny` row matches `U` at **any depth**, the
   decision is `DENY`.
3. **Allow rule.** Otherwise, if any applicable `allow` row matches `U` at depth
   `<= D`, the decision is `ALLOW`.
4. **Default.** Otherwise the decision is `DENY`.

Consequences, stated so they cannot be misread:

- **Deny always wins.** A deny cannot be overridden by an allow, whether the allow is
  on the same node, a deeper node, a closer group, or the user directly. There is no
  "more specific rule wins".
- **Allows are bounded, denies are not.** Membership paths longer than `D` never grant
  access, but they still cause a deny to apply. The reason is safety: ignoring a deny
  that sits just past the bound would grant access nobody intended.
- **Default is deny.** No matching allow means no access.
- A folder rule applies to everything beneath it, including nested folders.
- Two rows with the same node and subject but opposite effects: deny wins.

## 7. Cycles

The group graph may contain cycles (`G1 -> G2 -> G1`).

- Traversal must terminate. Each group is visited at most once per traversal.
- A cycle never grants access by itself, and never raises an error.
- Depth is always the shortest path, so a cycle does not make a depth appear larger
  or smaller than the shortest path implies.
- The resolver may report that a cycle was encountered (a boolean flag). The flag does
  not change the decision.

## 8. Authorised set

`authorised(U)` is the set of all documents `d` for which the decision is `ALLOW`.
Folders are never returned; only documents.

## 9. Properties the implementations must satisfy

1. **Determinism.** Same snapshot, same result.
2. **Deny is monotonic.** Adding a deny row never turns a `DENY` into `ALLOW`.
3. **Allow is monotonic.** Adding an allow row never turns an `ALLOW` into `DENY`.
4. **Row order is irrelevant.** Shuffling `member_of` or `acl` does not change results.
5. **Incremental equals scratch.** After any sequence of membership changes, the fast
   resolver's answers equal a full recomputation from scratch.

## 10. Out of scope

Row expiry, ownership, "everyone" or public groups, negative membership, per-document
sensitivity tiers (tiers only guide the data generator), and time of day.

## 11. Worked examples (named test cases)

Notation: `A -> B` means A is a direct member of B. `F > d` means folder F contains d.
`allow G on d` is an ACL row. Every example is a named test in `tests/unit/`.

| # | Name | Setup | Result |
|---|------|-------|--------|
| 1 | default_deny | u1 in no group; d1 has no rows | DENY |
| 2 | direct_user_allow | allow u1 on d1 | ALLOW |
| 3 | group_allow | u1 -> G1; allow G1 on d1 | ALLOW |
| 4 | nested_depth_2 | u1 -> G1 -> G2; allow G2 on d1 | ALLOW |
| 5 | depth_exactly_bound | u1 -> G1 -> G2 -> G3 -> G4; allow G4 on d1 | ALLOW |
| 6 | depth_past_bound | u1 -> G1 -> G2 -> G3 -> G4 -> G5; allow G5 on d1 | DENY |
| 7 | deny_beats_allow_same_node | u1 -> G1; allow G1 on d1; deny u1 on d1 | DENY |
| 8 | deny_via_nested_group | u1 -> G1; u1 -> G2 -> G3; allow G1 on d1; deny G3 on d1 | DENY |
| 9 | folder_deny_beats_doc_allow | F1 > d1; u1 -> G1; deny G1 on F1; allow G1 on d1 | DENY |
| 10 | folder_allow_inherited | F1 > d1; u1 -> G1; allow G1 on F1 | ALLOW |
| 11 | nested_folder_inherited | F1 > F2 > d1; u1 -> G1; allow G1 on F1 | ALLOW |
| 12 | deny_past_bound_still_applies | u1 -> G1; u1 -> H1 -> H2 -> H3 -> H4 -> H5; allow G1 on d1; deny H5 on d1 | DENY |
| 13 | cycle_reaches_group | u1 -> G1; G1 -> G2; G2 -> G1; allow G2 on d1 | ALLOW |
| 14 | cycle_no_membership_no_grant | G1 -> G2; G2 -> G1; u1 in neither; allow G1 on d1 | DENY |
| 15 | deny_inside_cycle | u1 -> G1; G1 -> G2; G2 -> G1; allow G1 on d1; deny G2 on d1 | DENY |
| 16 | unrelated_deny_ignored | u1 -> G1; allow G1 on d1; deny G9 on d1 (u1 not in G9) | ALLOW |
| 17 | rows_are_per_document | u1 -> G1; allow G1 on d1; deny G1 on d2 | d1 ALLOW, d2 DENY |
| 18 | shortest_path_counts | u1 -> G1 -> G2 -> G3 -> G4 -> G5; u1 -> G5; allow G5 on d1 | ALLOW |
| 19 | same_node_conflict | u1 -> G1; allow G1 on d1; deny G1 on d1 | DENY |
| 20 | deny_scoped_to_subtree | R > F1 > d1; R > F2 > d2; u1 -> G1; allow G1 on R; deny G1 on F1 | d1 DENY, d2 ALLOW |

Add five more before moving on (examples: a user with two allow paths and one deny
path; a deny directly on a user while their group has an allow on a parent folder;
a bound-exceeding chain that also contains a cycle; a folder three levels deep with
a deny in the middle; an empty graph).