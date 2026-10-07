-- migrate:up
CREATE TABLE chunks (
    id        bigserial PRIMARY KEY,
    doc_id    text NOT NULL,
    ord       integer NOT NULL,
    text      text NOT NULL,
    embedding vector(384) NOT NULL,
    tsv       tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    UNIQUE (doc_id, ord)
);
CREATE INDEX chunks_hnsw_idx ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_tsv_idx ON chunks USING gin (tsv);

-- migrate:down
DROP TABLE chunks;
