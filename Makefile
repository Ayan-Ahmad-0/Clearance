.PHONY: db migrate test lint bench-small bench

db:
	docker compose up -d

migrate:
	dbmate up

lint:
	ruff check .
	lint-imports

test:
	pytest -q

bench-small:
	@echo "TODO: later phase"

bench:
	@echo "TODO: later phase"
load-org:
	python scripts/load_org.py --org data/org/org.json
diff-full:
	python scripts/full_differential.py --org data/org/org.json