FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    ORG_DIR=data/org_nist \
    PYTHONPATH=/app/src:/app

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml requirements.txt ./
COPY src ./src

RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -e .

# Pre-cache the local embedding model during build to reduce cold-start latency
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"

COPY . .

EXPOSE 8000

CMD ["sh", "-c", "python scripts/init_db.py && uvicorn clearance.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
