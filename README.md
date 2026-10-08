# Clearance Permission-Aware Search & Answering

A permission-aware document search assistant that answers questions using only what each person is allowed to read. Access is worked out first and enforced inside the search itself, every answer is cited, every citation is checked against what that person was actually allowed to retrieve, and the assistant says so when the allowed documents don't contain the answer.

[![Python](https://img.shields.io/badge/Python-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/) [![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?style=flat&logo=postgresql&logoColor=white)](https://www.postgresql.org/) [![pgvector](https://img.shields.io/badge/pgvector-4169E1?style=flat)](https://github.com/pgvector/pgvector) [![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/) [![Gemini](https://img.shields.io/badge/Gemini-8E75B2?style=flat&logo=googlegemini&logoColor=white)](https://ai.google.dev/) [![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat&logo=docker&logoColor=white)](https://www.docker.com/) [![pytest](https://img.shields.io/badge/pytest-0A9EDC?style=flat&logo=pytest&logoColor=white)](https://pytest.org/) [![Hypothesis](https://img.shields.io/badge/Hypothesis-FFCC00?style=flat)](https://hypothesis.readthedocs.io/) [![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-2088FF?style=flat&logo=githubactions&logoColor=white)](https://github.com/features/actions) [![HTML5](https://img.shields.io/badge/HTML5-E34F26?style=flat&logo=html5&logoColor=white)](https://developer.mozilla.org/docs/Web/HTML) [![Cloudflare Pages](https://img.shields.io/badge/Cloudflare%20Pages-F38020?style=flat&logo=cloudflarepages&logoColor=white)](https://pages.cloudflare.com/)

**Live demo:** TODO · **Case study:** TODO

---

## 📋 Overview

Clearance is a question-answering system for organisations where not everyone may read everything. A normal search engine grabs the best matches from every document and hides the forbidden ones afterwards, which leaves restricted people with almost nothing. Clearance reverses the order: it works out what a person may read, then searches only inside that. It:

1. **Generates a synthetic organisation** (seed 42): 1,800 documents, users, nested groups, folders, and allow/deny rules, so every number is reproducible
2. **Adds real text**: seven public NIST SP 800 publications, attached to the organisation through an overlay with its own database and synthetic access rules
3. **Scans and chunks** every document, quarantining chunks that look like prompt-injection attacks before they are indexed
4. **Embeds** each chunk locally (`BAAI/bge-small-en-v1.5`) and indexes it in **Postgres + pgvector** (HNSW) alongside a full-text index
5. **Resolves access in SQL**: rules inherit down folders, groups can contain groups, and a deny always wins
6. **Retrieves with the access filter inside the query** (prefilter), and keeps a naive search-then-hide mode (postfilter) to measure what it costs
7. **Generates** a cited answer with a Gemini model from the allowed chunks only, treating chunk text as untrusted data and refusing when the allowed documents don't cover the question
8. **Verifies** every citation against the chunks that person was allowed to retrieve; invented or out-of-scope citations cause a refusal
9. Is **measured, not just demoed**: an independent reference resolver, property-based tests, a retrieval-starvation benchmark, an injection test set, and answer-level leak probes
10. Is served through a **static demo page** (precomputed examples plus a live question box) backed by a small **FastAPI** service

This mirrors how a permission-aware retrieval system should be validated: not just "does it find things," but "can someone ever receive something they shouldn't, and how much does restricting access hurt what they can find."

---

## 🏗️ Architecture

**Synthetic org + NIST PDFs** → **Scan & Chunk (Python)** → **Local Embeddings (bge-small-en-v1.5)** → **Postgres + pgvector** → **Access-filtered Retrieve + Generate (Gemini)** → **Demo page (FastAPI + static HTML)**

| Stage | Purpose | Format | Storage |
| --- | --- | --- | --- |
| **Generate** | Build the organisation: users, groups, folders, rules, documents | JSON / JSON lines | `data/org/` |
| **Overlay** | Add the 7 NIST PDFs with synthetic access rules | JSON / JSON lines | `data/org_nist/` |
| **Scan + Chunk** | Split documents, quarantine injection-like chunks | Text chunks | `quarantine.jsonl` beside the documents |
| **Embed + Index** | Local embeddings, stored as vectors | 384-dim vectors | Postgres + pgvector (HNSW + full-text) |
| **Resolve access** | Compute which documents a user may read | SQL function | Postgres (`authorised_documents`) |
| **Retrieve** | Top-K search inside the allowed set (prefilter) or over everything (postfilter) | Ranked chunks | In-memory per request |
| **Answer** | Cited response from allowed chunks only, citations verified | JSON → text + citations | Returned to the UI |
| **Serve** | Persona and question in, both retrieval modes out | — | FastAPI + static page |

### Architecture Diagram

![Architecture Diagram](images/architecture_diagram.png)

### Tech Stack

- **Data:** a seeded synthetic organisation (1,800 documents) plus seven public NIST SP 800 publications (SP 800-53, 800-171, 800-37, 800-137, 800-30, 800-63-3, 800-61), giving 4,099 chunks in the combined database
- **Access model:** folder inheritance, nested groups, allow and deny rules with deny-wins, resolved in Postgres, plus an independent pure-Python reference resolver used only for testing
- **Chunking and scanning:** paragraph-aware chunker; an ingest-time scanner flags exfiltration, role-spoofing, override, delimiter-escape, zero-width and encoded-payload patterns
- **Embeddings:** local `BAAI/bge-small-en-v1.5` (384 dimensions), so there are no embedding quota limits or costs
- **Vector store:** Postgres with the `pgvector` extension (HNSW index) and a generated `tsvector` column for full-text search, run in Docker
- **Retrieval:** two modes behind one function: **prefilter** (access rule inside the query) and **postfilter** (search everything, hide afterwards), so the cost of the naive approach can be measured
- **Generation:** Gemini (`gemini-3.1-flash-lite`), temperature 0, JSON output, strict system prompt; chunk text is escaped so it cannot close its own delimiter. A deterministic fake provider is the default, so tests and benchmarks never touch model quota
- **Answer verification:** every citation must refer to a chunk that was in that person's allowed results; otherwise the answer becomes a refusal
- **Testing:** pytest unit tests, a Hypothesis differential test (SQL resolver vs. reference resolver on random organisations), and a pinned snapshot hash for the seed-42 organisation checked in CI
- **Frontend:** a single static HTML page (persona selector, example questions, live question box, side-by-side prefilter vs. postfilter panels), hosted on Cloudflare Pages, backed by a FastAPI service with per-address and daily limits

---

## 📊 Demo Output

![Administrator view](images/demo_admin.png) ![Restricted view](images/demo_restricted.png)

*Screenshots: TODO. The administrator sees identical panels in both modes because nothing needs hiding; a restricted person sees the difference.*

---

## 🔄 Pipeline Flow

```
Generate synthetic organisation (seed 42) + add NIST PDFs (overlay)
        │
        ▼
Scan each chunk for injection patterns → quarantine flagged chunks
        │
        ▼
Embed locally (bge-small-en-v1.5) → Index in Postgres + pgvector (HNSW + full-text)
        │
        ▼
Question + who is asking → resolve allowed documents in SQL
        │
        ▼
Retrieve top-K inside the allowed set (prefilter)   [postfilter kept for comparison]
        │
        ▼
Generate cited answer (Gemini) from allowed chunks only
        │
        ▼
Verify citations against allowed chunks → answer, or honest refusal
        │
        ▼
Static demo page (Cloudflare Pages) ← FastAPI
```

Each stage writes its own files (`org.json`, `documents.jsonl`, `quarantine.jsonl`), so any stage can be re-run on its own. The NIST configuration lives in a separate database so the synthetic-only benchmark numbers stay reproducible.

---

## ✅ Evaluation Results

### Access rules match an independent reference

| Check | Result |
| --- | --- |
| SQL resolver vs. reference resolver, all user–document pairs | 720,000 pairs, 0 mismatches |
| Hypothesis differential test, random organisations | 300 examples passed, 0 failed (17% had membership beyond the depth bound; 70% had at least one user who can see documents) |
| Hypothesis test, incremental refresh equals full recompute | 150 examples passed, 0 failed |

### Retrieval does not starve restricted users

Broad questions on the synthetic organisation. `broad@10` is defined in `eval/` (TODO: one-line definition).

| Person | Can read (chunks) | Prefilter broad@10 | Postfilter broad@10 | Postfilter avg results (of 10) |
| --- | ---: | ---: | ---: | ---: |
| Administrator | 1,800 | 1.000 | 1.000 | 10.0 |
| Mid-visibility staff | 195 | 1.000 | 0.573 | 5.7 |
| Junior | 78 | 1.000 | 0.557 | 2.83 |
| New joiner | 18 | 1.000 | 0.696 | 0.8 |

### Nothing leaked

| Check | Result |
| --- | --- |
| Retrieval-level secret probes, synthetic data (planted strings, per persona) | 0 leaks |
| Answer-level smoke test, synthetic data (8 questions, Gemini) | 0 leaks |
| Answer-level test, real NIST text (20 questions × 4 personas, Gemini) | 0 answers cited a document the asker cannot read |
| NIST test outcomes | 11 answered with the right citation, 9 correctly refused (unreadable documents) |
| Hand check of the 11 answers against the NIST text | TODO of 11 correct |
| Tokens per question | ~1,230 in / ~36 out |
| Cost per question | TODO |

### Prompt-injection scanner (ingest-time)

| Label | Category | Documents | Flagged |
| --- | --- | ---: | ---: |
| Injected | override, role spoof, exfiltration, link exfil, delimiter escape, zero-width, base64 | 12 each | 12 of 12 each |
| Injected | paraphrased | 12 | 0 |
| Injected | other language | 12 | 0 |
| Benign | keyword overlap | 15 | 0 |
| Benign | quotes an attack | 15 | 15 |
| Benign | legitimate markup and links | 15 | 15 |

On real text: 0 of 1,800 synthetic documents flagged; 1 of about 2,300 NIST chunks flagged (a glossary passage in SP 800-63-3, reviewed by hand and judged a false positive).

### Ingest and index

| Run | Chunks | Time |
| --- | ---: | ---: |
| Synthetic organisation | 1,800 | 94.7 s |
| Synthetic + NIST | 4,099 (1 quarantined) | 432.8 s |

TODO: latency table (p50 / p95 per persona per mode), 60,000 and 400,000 chunk index results, index build time, revocation timing, from `benchmarks/results/`.

**Machine:** Windows 11, Intel CPU (4 physical / 8 logical cores), 15.7 GB RAM, Python 3.13.5, Postgres with pgvector in Docker. TODO: exact CPU name, Docker memory limit, pgvector version.

---

## 🗂️ Access Store (Postgres + pgvector)

| Object | Description |
| --- | --- |
| `principals`, `membership`, `membership_closure` | Users, groups, and the precomputed closure of nested group membership |
| `nodes`, `node_ancestors` | Folder and document tree with precomputed ancestors for inheritance |
| `acl` | Allow and deny rules attached to folders and documents |
| `chunks` | One row per chunk: document id, order, text, 384-dim embedding, generated `tsvector` |
| `authorised_documents(user, depth)` | SQL function returning the documents a user may read; raises on unknown users |

---

## 🔒 Grounding & Security

- **Access enforced inside the search.** Only chunks from documents the person may read can be retrieved, so nothing outside that set can reach the model
- **Independent reference check.** A separate pure-Python resolver computes the same rules; tests compare the two on every pair and on random organisations
- **Chunk text is data, not instructions.** Retrieved text is wrapped in delimiters the text cannot close, and the system prompt tells the model to ignore instructions inside sources
- **Citation verification.** Invented citations and citations outside the allowed set turn the answer into a refusal
- **Explicit refusal.** When no authorised evidence exists the model is never called; when it exists but doesn't answer the question, the model must refuse instead of guessing
- **Ingest scanner as a second layer.** It quarantines obvious attack patterns, but the architecture, not the scanner, is the main defence
- **Cost-aware testing.** A deterministic fake LLM is the default (`CLEARANCE_LLM=fake`), so tests and benchmarks run free and repeatable

---

## 📁 Repository Structure

```
clearance/
├── README.md
├── pyproject.toml
├── docker-compose.yml
├── Makefile
│
├── src/clearance/
│   ├── ingest/                    # chunker, embedder, injection scanner
│   ├── retrieval/                 # search (prefilter / postfilter)
│   ├── answering/                 # LLM adapter (fake / Gemini), answer + citation checks
│   └── api/                       # FastAPI demo service
│
├── scripts/
│   ├── gen_org.py                 # seeded synthetic organisation
│   ├── load_org.py                # load organisation into Postgres
│   ├── ingest_org.py              # chunk, scan, embed, index
│   ├── add_nist.py                # NIST overlay
│   ├── run_eval.py                # retrieval scorecard
│   ├── run_injection_eval.py      # scanner evaluation
│   ├── smoke_llm.py               # answer-level smoke test, synthetic
│   ├── smoke_nist.py              # answer-level smoke test, NIST
│   ├── build_demo.py              # precompute demo/demo.json
│   └── machine_spec.py            # hardware record for benchmarks
│
├── tests/
│   ├── unit/                      # access cases, answering, pinned snapshot hash
│   ├── property/                  # Hypothesis differential tests
│   └── cases.py                   # shared test helpers
│
├── data/
│   ├── org/                       # synthetic organisation (seed 42)
│   ├── org_nist/                  # NIST overlay
│   └── nist/                      # the 7 source PDFs
│
├── eval/                          # evaluation sets and results
├── benchmarks/results/            # raw benchmark output and machine spec
└── demo/
    ├── index.html                 # static demo page
    └── demo.json                  # precomputed example answers
```

---

## ⚙️ Configuration

Set these in your environment or a `.env` file in the project root:

```
DATABASE_URL=postgresql://clearance:clearance@localhost:5433/clearance
GEMINI_API_KEY=your_gemini_api_key
CLEARANCE_LLM=fake            # fake (default) or gemini
GEMINI_MODEL=gemini-3.1-flash-lite
```

For the API service:

```
ORG_DIR=data/org_nist
ALLOWED_ORIGINS=http://localhost:8000
RATE_PER_IP=10                # questions per 10 minutes per address
DAILY_CAP=300                 # live questions per day, all visitors
```

- The synthetic benchmarks use the `clearance` database; the NIST configuration uses a separate `clearance_nist` database
- Running `pytest` can change the database contents. Reload the organisation and re-ingest before benchmarks or demos
- The API key stays on the server and is never sent to the browser

---

## 🚀 Getting Started

```
# Clone the repo
git clone https://github.com/Ayan-Ahmad-0/clearance.git
cd clearance

# Install dependencies
pip install -e .

# Start Postgres + pgvector and apply migrations
docker compose up -d
dbmate up

# Synthetic organisation: generate, load, ingest
python scripts/gen_org.py --seed 42
python scripts/load_org.py --org data/org/org.json
python scripts/ingest_org.py

# Tests and evaluation
pytest tests -rs
python scripts/run_eval.py
python scripts/run_injection_eval.py --rebuild

# NIST overlay (separate database)
python scripts/add_nist.py --org data/org --nist data/nist --out data/org_nist
python scripts/load_org.py --org data/org_nist/org.json
python scripts/ingest_org.py --docs data/org_nist/documents.jsonl --dsn $DATABASE_URL

# Answer-level smoke test (needs GEMINI_API_KEY)
CLEARANCE_LLM=gemini python scripts/smoke_nist.py

# Build and run the demo
python scripts/build_demo.py
uvicorn clearance.api.app:app --port 8001
python -m http.server -d demo 8000
```

| Service | URL |
| --- | --- |
| Demo page (local) | <http://localhost:8000> |
| API (local) | <http://localhost:8001> |
| Demo page (live) | TODO |
| Database | Postgres + pgvector in Docker, `localhost:5433` |

---

## 🚧 Challenges Solved

- Found that the naive approach (search everything, hide forbidden results afterwards) **starves restricted users**: the most restricted persona averaged 0.8 results out of 10, because the best global matches sit in documents they cannot read. Prefilter returns a full list; the benchmark table above is the evidence
- Kept the access rules honest by writing a **second, independent resolver** and comparing it with the SQL version across 720,000 pairs and thousands of random organisations, instead of trusting a single implementation
- Discovered that running the property tests **overwrites the organisation in the database**, which produced an "unknown user" error during a later smoke test; fixed by reloading and re-ingesting before benchmarks, and by keeping the NIST data in its own database
- Adding 492-page NIST documents would have changed every synthetic benchmark; solved with an **overlay** that leaves `data/org` untouched, plus a check that the three most restricted personas still see exactly what they saw before and see no NIST document
- Found that the ingest script **overwrote the quarantine file** of the previous run and wrote it to a hardcoded path; fixed by writing it beside the documents being ingested
- Reviewed the one NIST chunk the scanner quarantined and traced it to a **false positive** on a glossary page; recorded it as a measured cost of the scanner instead of loosening the rule to hide it
- Noticed that the embedding model truncates long input, then measured chunk lengths on real text (max 3,609 characters, average 938, 16 chunks over 2,000) and accepted the small tail loss, since the full text is still searchable through the full-text index
- Avoided model-quota failures in tests and benchmarks by hiding the LLM behind a small adapter with a **deterministic fake provider** as the default
- Pinned the seed-42 organisation's snapshot hash in a test that runs in CI, so benchmark results can be regenerated rather than trusted

---

## ⚠️ Known Limitations

- **Authentication is out of scope.** Clearance decides what a known person may read and assumes the surrounding application has already verified who is asking. The demo's person selector stands in for a login so visitors can compare four roles; in a real deployment users would never choose who they are
- **"Zero leaks" is a measured result for these runs**, not a proof of security
- **No answer-quality benchmark.** I did not build an LLM judge or a large hand-labelled set; answers were checked by hand on a small set only
- **Index limitation at scale:** at 400,000 chunks the most restricted persona received 8.9 of 10 results from prefilter with default index settings; I did not rerun that benchmark with a higher `hnsw.max_scan_tuples`
- **Scanner limits:** misses paraphrased and translated attacks; flags benign documents that quote attacks or contain markup. Its test corpus and rules were written by the same person, so its 100% rows show it does what it was written to do, not that it catches unanticipated attacks
- **Benchmarks ran on one laptop**, and the larger index benchmarks use synthetic vectors
- **Access rules and the organisation are synthetic.** The NIST text is real and public; no private data is used
- Example answers in the demo are precomputed; typed questions run live and are rate-limited

---

## 🛠️ Future Improvements

- Build an LLM judge with a hand-labelled set, and report answer quality per retrieval mode
- Rerun the 400,000-chunk benchmark with a higher `hnsw.max_scan_tuples` and report how prefilter recovers
- Add hybrid retrieval (full-text + vector) and compare it on restricted users
- Plug in real authentication (single sign-on tokens) so the API takes the user from a verified identity instead of a demo selector
- Record a short demo walkthrough of the deployed assistant