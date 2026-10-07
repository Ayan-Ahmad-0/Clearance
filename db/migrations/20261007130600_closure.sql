-- migrate:up

-- Shortest membership depth from each user to every group they reach.
-- All depths are stored: denies ignore the bound, so depth 5+ rows must exist.
CREATE TABLE membership_closure (
    user_id    text    NOT NULL,
    user_kind  text    NOT NULL DEFAULT 'user' CHECK (user_kind = 'user'),
    group_id   text    NOT NULL,
    group_kind text    NOT NULL DEFAULT 'group' CHECK (group_kind = 'group'),
    depth      integer NOT NULL CHECK (depth >= 1),
    PRIMARY KEY (user_id, group_id),
    FOREIGN KEY (user_id, user_kind) REFERENCES principals (id, kind) ON DELETE CASCADE,
    FOREIGN KEY (group_id, group_kind) REFERENCES principals (id, kind) ON DELETE CASCADE
);
CREATE INDEX membership_closure_group_idx ON membership_closure (group_id);

-- Recompute the closure for the given users only.
-- Cycles are safe: the walk is capped at the number of groups, and the shortest
-- depth per group wins. A user never appears as their own ancestor.
CREATE FUNCTION refresh_membership_closure(p_users text[]) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    cap integer;
BEGIN
    SELECT count(*) INTO cap FROM principals WHERE kind = 'group';
    DELETE FROM membership_closure WHERE user_id = ANY (p_users);
    INSERT INTO membership_closure (user_id, group_id, depth)
    WITH RECURSIVE walk (user_id, group_id, depth) AS (
        SELECT m.child, m.parent, 1
        FROM membership m
        JOIN principals p ON p.id = m.child AND p.kind = 'user'
        WHERE m.child = ANY (p_users)
        UNION
        SELECT w.user_id, m.parent, w.depth + 1
        FROM walk w
        JOIN membership m ON m.child = w.group_id
        WHERE w.depth < cap
    )
    SELECT user_id, group_id, min(depth) FROM walk GROUP BY user_id, group_id;
END $$;

-- Users whose closure can change when an edge with this child changes:
-- the child itself and everything below it. Call it BEFORE deleting a group,
-- and after adding or removing a single edge.
CREATE FUNCTION users_below(p_roots text[]) RETURNS text[]
LANGUAGE sql STABLE AS $$
    WITH RECURSIVE below AS (
        SELECT id FROM principals WHERE id = ANY (p_roots)
        UNION
        SELECT m.child FROM membership m JOIN below b ON m.parent = b.id
    )
    SELECT coalesce(array_agg(b.id), '{}')
    FROM below b JOIN principals p ON p.id = b.id
    WHERE p.kind = 'user'
$$;

-- Inclusive ancestors of every node (a node is its own ancestor).
CREATE TABLE node_ancestors (
    node_id     text NOT NULL REFERENCES nodes (id) ON DELETE CASCADE,
    ancestor_id text NOT NULL REFERENCES nodes (id) ON DELETE CASCADE,
    PRIMARY KEY (node_id, ancestor_id)
);
CREATE INDEX node_ancestors_ancestor_idx ON node_ancestors (ancestor_id);

-- Recompute ancestors for the given nodes and everything below them.
-- New node: refresh_node_ancestors(ARRAY[id]).
-- Moved folder: refresh_node_ancestors(ARRAY[folder_id]).
-- Full rebuild: pass the root folders.
CREATE FUNCTION refresh_node_ancestors(p_roots text[]) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM node_ancestors
    WHERE node_id IN (
        WITH RECURSIVE down AS (
            SELECT id FROM nodes WHERE id = ANY (p_roots)
            UNION
            SELECT n.id FROM nodes n JOIN down d ON n.parent_id = d.id
        )
        SELECT id FROM down
    );
    INSERT INTO node_ancestors (node_id, ancestor_id)
    WITH RECURSIVE down AS (
        SELECT id FROM nodes WHERE id = ANY (p_roots)
        UNION
        SELECT n.id FROM nodes n JOIN down d ON n.parent_id = d.id
    ),
    up (node_id, ancestor_id, parent_id) AS (
        SELECT id, id, parent_id FROM nodes WHERE id IN (SELECT id FROM down)
        UNION ALL
        SELECT up.node_id, n.id, n.parent_id FROM up JOIN nodes n ON n.id = up.parent_id
    )
    SELECT node_id, ancestor_id FROM up;
END $$;

-- migrate:down
DROP FUNCTION refresh_node_ancestors(text[]);
DROP TABLE node_ancestors;
DROP FUNCTION users_below(text[]);
DROP FUNCTION refresh_membership_closure(text[]);
DROP TABLE membership_closure;