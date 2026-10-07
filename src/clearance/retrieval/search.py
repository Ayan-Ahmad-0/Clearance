"""Hybrid search with the authorised set inside the same SQL statement.

Vector (HNSW) and lexical (tsvector) lists are fused with RRF, k=60.
  prefilter  : each list is limited to the user's authorised documents before ranking
  postfilter : each list is the global top `pool`, filtered afterwards (the baseline
               that starves restricted users)
"""
from clearance.ingest.embedder import embed_query, to_pgvector

RRF_K = 60

_AUTH = """
WITH auth AS (
    SELECT doc_id FROM authorised_documents(%(user)s)
),
"""

_TAIL = """
SELECT c.id AS chunk_id, c.doc_id, c.text, f.score
FROM fused f JOIN chunks c ON c.id = f.id
ORDER BY f.score DESC
LIMIT %(top)s
"""

_PREFILTER = (
    _AUTH
    + """
vec AS (
    SELECT id, row_number() OVER (ORDER BY embedding <=> %(qv)s::vector) AS rnk
    FROM chunks
    WHERE doc_id IN (SELECT doc_id FROM auth)
    ORDER BY embedding <=> %(qv)s::vector
    LIMIT %(pool)s
),
lex AS (
    SELECT c.id, row_number() OVER (ORDER BY ts_rank_cd(c.tsv, tq.query) DESC) AS rnk
    FROM chunks c, websearch_to_tsquery('english', %(q)s) AS tq(query)
    WHERE c.tsv @@ tq.query AND c.doc_id IN (SELECT doc_id FROM auth)
    ORDER BY ts_rank_cd(c.tsv, tq.query) DESC
    LIMIT %(pool)s
),
fused AS (
    SELECT id, sum(1.0 / (%(k)s + rnk)) AS score
    FROM (SELECT id, rnk FROM vec UNION ALL SELECT id, rnk FROM lex) t
    GROUP BY id
)
"""
    + _TAIL
)

_POSTFILTER = (
    _AUTH
    + """
vec_all AS (
    SELECT id, doc_id, row_number() OVER (ORDER BY embedding <=> %(qv)s::vector) AS rnk
    FROM chunks
    ORDER BY embedding <=> %(qv)s::vector
    LIMIT %(pool)s
),
lex_all AS (
    SELECT c.id, c.doc_id, row_number() OVER (ORDER BY ts_rank_cd(c.tsv, tq.query) DESC) AS rnk
    FROM chunks c, websearch_to_tsquery('english', %(q)s) AS tq(query)
    WHERE c.tsv @@ tq.query
    ORDER BY ts_rank_cd(c.tsv, tq.query) DESC
    LIMIT %(pool)s
),
fused AS (
    SELECT id, sum(1.0 / (%(k)s + rnk)) AS score
    FROM (SELECT id, doc_id, rnk FROM vec_all UNION ALL SELECT id, doc_id, rnk FROM lex_all) t
    WHERE doc_id IN (SELECT doc_id FROM auth)
    GROUP BY id
)
"""
    + _TAIL
)

MODES = {"prefilter": _PREFILTER, "postfilter": _POSTFILTER}


def search(conn, user, query, *, mode="prefilter", top=10, pool=50, ef_search=400):
    """Return up to `top` chunks the user may read, best first."""
    params = {
        "user": user,
        "q": query,
        "qv": to_pgvector(embed_query(query)),
        "pool": pool,
        "k": RRF_K,
        "top": top,
    }
    iterative = "relaxed_order" if mode == "prefilter" else "off"
    with conn.transaction():
        conn.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(ef_search),))
        conn.execute("SELECT set_config('hnsw.iterative_scan', %s, true)", (iterative,))
        cur = conn.execute(MODES[mode], params)
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]