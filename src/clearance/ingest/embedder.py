"""bge-small-en-v1.5 behind a small adapter, so the model can be swapped later."""
from functools import lru_cache

MODEL = "BAAI/bge-small-en-v1.5"  # 384 dimensions
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(MODEL)


def embed_passages(texts, batch_size=64):
    vectors = _model().encode(
        texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False
    )
    return vectors.tolist()


def embed_query(query):
    return _model().encode([QUERY_PREFIX + query], normalize_embeddings=True)[0].tolist()


def to_pgvector(vector):
    return "[" + ",".join(f"{x:.6f}" for x in vector) + "]"