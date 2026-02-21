.PHONY: help docker-up docker-down migrate seed lint test test-unit test-integration eval clean

help:
	@echo "Creator GraphRAG — Available Commands"
	@echo ""
	@echo "  make docker-up          Start all services (docker-compose)"
	@echo "  make docker-down        Stop all services"
	@echo "  make migrate            Run Alembic migrations"
	@echo "  make seed               Seed development data"
	@echo "  make init-stores        Initialize Qdrant collections + Neo4j indexes"
	@echo "  make lint               Run ruff + mypy"
	@echo "  make test               Run all tests"
	@echo "  make test-unit          Run unit tests only"
	@echo "  make test-integration   Run integration tests only"
	@echo "  make eval               Run golden query evaluation"
	@echo "  make clean              Remove __pycache__ and .pyc files"

docker-up:
	docker-compose up -d
	@echo "Services started. API: http://localhost:8000/docs"

docker-down:
	docker-compose down

migrate:
	cd apps/api && alembic upgrade head

migrate-down:
	cd apps/api && alembic downgrade -1

migrate-gen:
	cd apps/api && alembic revision --autogenerate -m "$(msg)"

seed:
	python scripts/dev_seed.py

init-stores:
	python scripts/init_qdrant.py
	python scripts/init_neo4j.py

lint:
	ruff check apps/ libs/
	mypy apps/api/app apps/worker/app --ignore-missing-imports

format:
	ruff format apps/ libs/

test:
	pytest apps/api/tests apps/worker/tests -v

test-unit:
	pytest apps/api/tests/unit apps/worker/tests/unit -v

test-integration:
	pytest apps/api/tests/integration apps/worker/tests/integration -v --timeout=60

eval:
	python scripts/eval_run.py --queries tests/golden_queries/golden_queries.jsonl

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name "*.pyo" -delete 2>/dev/null || true
