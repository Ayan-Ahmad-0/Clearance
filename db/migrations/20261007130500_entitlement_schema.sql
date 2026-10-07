-- migrate:up
CREATE TABLE principals (
    id   text PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('user', 'group')),
    UNIQUE (id, kind),
    CHECK ((kind = 'user' AND id LIKE 'u%') OR (kind = 'group' AND id LIKE 'G%'))
);

-- child is a user or a group; parent must be a group
CREATE TABLE membership (
    child       text NOT NULL REFERENCES principals (id) ON DELETE CASCADE,
    parent      text NOT NULL,
    parent_kind text NOT NULL DEFAULT 'group' CHECK (parent_kind = 'group'),
    PRIMARY KEY (child, parent),
    FOREIGN KEY (parent, parent_kind) REFERENCES principals (id, kind) ON DELETE CASCADE
);
CREATE INDEX membership_parent_idx ON membership (parent);

-- spec section 2: a node is a document iff its id starts with d.
-- parent_kind pins every parent to a folder, so documents are leaves.
CREATE TABLE nodes (
    id          text PRIMARY KEY,
    kind        text NOT NULL CHECK (kind IN ('folder', 'document')),
    parent_id   text,
    parent_kind text NOT NULL DEFAULT 'folder' CHECK (parent_kind = 'folder'),
    UNIQUE (id, kind),
    CHECK ((kind = 'document' AND id LIKE 'd%') OR (kind = 'folder' AND id LIKE 'F%')),
    CHECK (parent_id IS DISTINCT FROM id),
    FOREIGN KEY (parent_id, parent_kind) REFERENCES nodes (id, kind) ON DELETE CASCADE
);
CREATE INDEX nodes_parent_idx ON nodes (parent_id);

-- spec section 3: the folder tree must not contain a cycle (invalid snapshot)
CREATE FUNCTION nodes_reject_cycle() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.parent_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF EXISTS (
        WITH RECURSIVE up AS (
            SELECT id, parent_id FROM nodes WHERE id = NEW.parent_id
            UNION
            SELECT n.id, n.parent_id FROM nodes n JOIN up ON n.id = up.parent_id
        )
        SELECT 1 FROM up WHERE id = NEW.id
    ) THEN
        RAISE EXCEPTION 'folder tree cycle at %', NEW.id USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER nodes_no_cycle
    BEFORE INSERT OR UPDATE OF parent_id ON nodes
    FOR EACH ROW EXECUTE FUNCTION nodes_reject_cycle();

-- one row per (node, subject, effect): allow and deny on the same pair can coexist (case 19)
CREATE TABLE acl (
    node_id    text NOT NULL REFERENCES nodes (id) ON DELETE CASCADE,
    subject_id text NOT NULL REFERENCES principals (id) ON DELETE CASCADE,
    effect     text NOT NULL CHECK (effect IN ('allow', 'deny')),
    PRIMARY KEY (node_id, subject_id, effect)
);
CREATE INDEX acl_subject_idx ON acl (subject_id, effect);

-- migrate:down
DROP TABLE acl;
DROP TRIGGER nodes_no_cycle ON nodes;
DROP FUNCTION nodes_reject_cycle();
DROP TABLE nodes;
DROP TABLE membership;
DROP TABLE principals;