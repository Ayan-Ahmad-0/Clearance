\restrict dbmate

-- Dumped from database version 16.15 (Debian 16.15-1.pgdg12+2)
-- Dumped by pg_dump version 18.6

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


--
-- Name: authorised_documents(text, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.authorised_documents(p_user text, p_bound integer DEFAULT 4) RETURNS TABLE(doc_id text)
    LANGUAGE plpgsql STABLE
    AS $$
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


--
-- Name: can_read(text, text, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.can_read(p_user text, p_node text, p_bound integer DEFAULT 4) RETURNS boolean
    LANGUAGE plpgsql STABLE
    AS $$
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


--
-- Name: nodes_reject_cycle(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.nodes_reject_cycle() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: refresh_membership_closure(text[]); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.refresh_membership_closure(p_users text[]) RETURNS void
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: refresh_node_ancestors(text[]); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.refresh_node_ancestors(p_roots text[]) RETURNS void
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: users_below(text[]); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.users_below(p_roots text[]) RETURNS text[]
    LANGUAGE sql STABLE
    AS $$
    WITH RECURSIVE below AS (
        SELECT id FROM principals WHERE id = ANY (p_roots)
        UNION
        SELECT m.child FROM membership m JOIN below b ON m.parent = b.id
    )
    SELECT coalesce(array_agg(b.id), '{}')
    FROM below b JOIN principals p ON p.id = b.id
    WHERE p.kind = 'user'
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: acl; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.acl (
    node_id text NOT NULL,
    subject_id text NOT NULL,
    effect text NOT NULL,
    CONSTRAINT acl_effect_check CHECK ((effect = ANY (ARRAY['allow'::text, 'deny'::text])))
);


--
-- Name: chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chunks (
    id bigint NOT NULL,
    doc_id text NOT NULL,
    ord integer NOT NULL,
    text text NOT NULL,
    embedding public.vector(384) NOT NULL,
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('english'::regconfig, text)) STORED
);


--
-- Name: chunks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.chunks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: chunks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.chunks_id_seq OWNED BY public.chunks.id;


--
-- Name: membership; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.membership (
    child text NOT NULL,
    parent text NOT NULL,
    parent_kind text DEFAULT 'group'::text NOT NULL,
    CONSTRAINT membership_parent_kind_check CHECK ((parent_kind = 'group'::text))
);


--
-- Name: membership_closure; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.membership_closure (
    user_id text NOT NULL,
    user_kind text DEFAULT 'user'::text NOT NULL,
    group_id text NOT NULL,
    group_kind text DEFAULT 'group'::text NOT NULL,
    depth integer NOT NULL,
    CONSTRAINT membership_closure_depth_check CHECK ((depth >= 1)),
    CONSTRAINT membership_closure_group_kind_check CHECK ((group_kind = 'group'::text)),
    CONSTRAINT membership_closure_user_kind_check CHECK ((user_kind = 'user'::text))
);


--
-- Name: node_ancestors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.node_ancestors (
    node_id text NOT NULL,
    ancestor_id text NOT NULL
);


--
-- Name: nodes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.nodes (
    id text NOT NULL,
    kind text NOT NULL,
    parent_id text,
    parent_kind text DEFAULT 'folder'::text NOT NULL,
    CONSTRAINT nodes_check CHECK ((((kind = 'document'::text) AND (id ~~ 'd%'::text)) OR ((kind = 'folder'::text) AND (id ~~ 'F%'::text)))),
    CONSTRAINT nodes_check1 CHECK ((parent_id IS DISTINCT FROM id)),
    CONSTRAINT nodes_kind_check CHECK ((kind = ANY (ARRAY['folder'::text, 'document'::text]))),
    CONSTRAINT nodes_parent_kind_check CHECK ((parent_kind = 'folder'::text))
);


--
-- Name: principals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.principals (
    id text NOT NULL,
    kind text NOT NULL,
    CONSTRAINT principals_check CHECK ((((kind = 'user'::text) AND (id ~~ 'u%'::text)) OR ((kind = 'group'::text) AND (id ~~ 'G%'::text)))),
    CONSTRAINT principals_kind_check CHECK ((kind = ANY (ARRAY['user'::text, 'group'::text])))
);


--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_migrations (
    version character varying NOT NULL
);


--
-- Name: chunks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunks ALTER COLUMN id SET DEFAULT nextval('public.chunks_id_seq'::regclass);


--
-- Name: acl acl_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.acl
    ADD CONSTRAINT acl_pkey PRIMARY KEY (node_id, subject_id, effect);


--
-- Name: chunks chunks_doc_id_ord_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT chunks_doc_id_ord_key UNIQUE (doc_id, ord);


--
-- Name: chunks chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT chunks_pkey PRIMARY KEY (id);


--
-- Name: membership_closure membership_closure_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.membership_closure
    ADD CONSTRAINT membership_closure_pkey PRIMARY KEY (user_id, group_id);


--
-- Name: membership membership_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.membership
    ADD CONSTRAINT membership_pkey PRIMARY KEY (child, parent);


--
-- Name: node_ancestors node_ancestors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.node_ancestors
    ADD CONSTRAINT node_ancestors_pkey PRIMARY KEY (node_id, ancestor_id);


--
-- Name: nodes nodes_id_kind_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.nodes
    ADD CONSTRAINT nodes_id_kind_key UNIQUE (id, kind);


--
-- Name: nodes nodes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.nodes
    ADD CONSTRAINT nodes_pkey PRIMARY KEY (id);


--
-- Name: principals principals_id_kind_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.principals
    ADD CONSTRAINT principals_id_kind_key UNIQUE (id, kind);


--
-- Name: principals principals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.principals
    ADD CONSTRAINT principals_pkey PRIMARY KEY (id);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: acl_subject_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX acl_subject_idx ON public.acl USING btree (subject_id, effect);


--
-- Name: chunks_hnsw_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX chunks_hnsw_idx ON public.chunks USING hnsw (embedding public.vector_cosine_ops);


--
-- Name: chunks_tsv_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX chunks_tsv_idx ON public.chunks USING gin (tsv);


--
-- Name: membership_closure_group_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX membership_closure_group_idx ON public.membership_closure USING btree (group_id);


--
-- Name: membership_parent_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX membership_parent_idx ON public.membership USING btree (parent);


--
-- Name: node_ancestors_ancestor_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX node_ancestors_ancestor_idx ON public.node_ancestors USING btree (ancestor_id);


--
-- Name: nodes_parent_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX nodes_parent_idx ON public.nodes USING btree (parent_id);


--
-- Name: nodes nodes_no_cycle; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER nodes_no_cycle BEFORE INSERT OR UPDATE OF parent_id ON public.nodes FOR EACH ROW EXECUTE FUNCTION public.nodes_reject_cycle();


--
-- Name: acl acl_node_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.acl
    ADD CONSTRAINT acl_node_id_fkey FOREIGN KEY (node_id) REFERENCES public.nodes(id) ON DELETE CASCADE;


--
-- Name: acl acl_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.acl
    ADD CONSTRAINT acl_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.principals(id) ON DELETE CASCADE;


--
-- Name: membership membership_child_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.membership
    ADD CONSTRAINT membership_child_fkey FOREIGN KEY (child) REFERENCES public.principals(id) ON DELETE CASCADE;


--
-- Name: membership_closure membership_closure_group_id_group_kind_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.membership_closure
    ADD CONSTRAINT membership_closure_group_id_group_kind_fkey FOREIGN KEY (group_id, group_kind) REFERENCES public.principals(id, kind) ON DELETE CASCADE;


--
-- Name: membership_closure membership_closure_user_id_user_kind_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.membership_closure
    ADD CONSTRAINT membership_closure_user_id_user_kind_fkey FOREIGN KEY (user_id, user_kind) REFERENCES public.principals(id, kind) ON DELETE CASCADE;


--
-- Name: membership membership_parent_parent_kind_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.membership
    ADD CONSTRAINT membership_parent_parent_kind_fkey FOREIGN KEY (parent, parent_kind) REFERENCES public.principals(id, kind) ON DELETE CASCADE;


--
-- Name: node_ancestors node_ancestors_ancestor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.node_ancestors
    ADD CONSTRAINT node_ancestors_ancestor_id_fkey FOREIGN KEY (ancestor_id) REFERENCES public.nodes(id) ON DELETE CASCADE;


--
-- Name: node_ancestors node_ancestors_node_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.node_ancestors
    ADD CONSTRAINT node_ancestors_node_id_fkey FOREIGN KEY (node_id) REFERENCES public.nodes(id) ON DELETE CASCADE;


--
-- Name: nodes nodes_parent_id_parent_kind_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.nodes
    ADD CONSTRAINT nodes_parent_id_parent_kind_fkey FOREIGN KEY (parent_id, parent_kind) REFERENCES public.nodes(id, kind) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict dbmate


--
-- Dbmate schema migrations
--

INSERT INTO public.schema_migrations (version) VALUES
    ('20261006103033'),
    ('20261007064855'),
    ('20261007065111'),
    ('20261007101245'),
    ('20261007104824'),
    ('20261007130500'),
    ('20261007130600'),
    ('20261007152000'),
    ('20261007155500');
