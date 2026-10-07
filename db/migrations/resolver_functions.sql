-- migrate:up

-- Spec section 6, in SQL.
--   subjects : the user at depth 0, plus every group in the user's closure
--   hits     : ACL rows on the document or any inclusive ancestor, matching a subject
--   decision : any deny at ANY depth -> DENY; else any allow at depth <= bound -> ALLOW;
--              else DENY (default deny)
CREATE FUNCTION authorised_documents(p_user text, p_bound integer DEFAULT 4)
RETURNS TABLE (doc_id text)
LANGUAGE plpgsql STABLE AS $$
BEGIN
    PERFORM 1 FROM principals WHERE id = p_user AND kind = 'user';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown user %', p_user USING ERRCODE = 'no_data_found';
    END IF;
    RETURN QUERY
    WITH subjects AS (
        SELECT p_user AS subject_id, 0 AS depth
        UNION ALL
        SELECT mc.group_id, mc.depth FROM membership_closure mc WHERE mc.user_id = p_user
    ),
    hits AS (
        SELECT na.node_id AS d_id, a.effect, s.depth
        FROM subjects s
        JOIN acl a ON a.subject_id = s.subject_id
        JOIN node_ancestors na ON na.ancestor_id = a.node_id
        JOIN nodes n ON n.id = na.node_id AND n.kind = 'document'
    )
    SELECT h.d_id
    FROM hits h
    GROUP BY h.d_id
    HAVING bool_or(h.effect = 'allow' AND h.depth <= p_bound)
       AND NOT bool_or(h.effect = 'deny');
END $$;

-- Point decision for one node. A separate query on purpose (it must not build the
-- whole authorised set); the tests assert it agrees with authorised_documents.
CREATE FUNCTION can_read(p_user text, p_node text, p_bound integer DEFAULT 4)
RETURNS boolean
LANGUAGE plpgsql STABLE AS $$
BEGIN
    PERFORM 1 FROM principals WHERE id = p_user AND kind = 'user';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown user %', p_user USING ERRCODE = 'no_data_found';
    END IF;
    RETURN coalesce((
        WITH subjects AS (
            SELECT p_user AS subject_id, 0 AS depth
            UNION ALL
            SELECT mc.group_id, mc.depth FROM membership_closure mc WHERE mc.user_id = p_user
        ),
        hits AS (
            SELECT a.effect, s.depth
            FROM node_ancestors na
            JOIN acl a ON a.node_id = na.ancestor_id
            JOIN subjects s ON s.subject_id = a.subject_id
            WHERE na.node_id = p_node
        )
        SELECT bool_or(effect = 'allow' AND depth <= p_bound) AND NOT bool_or(effect = 'deny')
        FROM hits
    ), false);
END $$;

-- migrate:down
DROP FUNCTION can_read(text, text, integer);
DROP FUNCTION authorised_documents(text, integer);