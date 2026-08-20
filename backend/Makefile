install:
	uv sync

dev:
	PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 --reload

gateway:
	PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001

test:
	PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run pytest tests/ -v

coverage:
	PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run pytest tests/ --cov=packages/harness/deerflow --cov-report=term-missing --cov-report=html:.deer-flow/htmlcov --ignore=tests/test_sandbox_orphan_reconciliation_e2e.py -q

test-blocking-io:
	PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run pytest tests/blocking_io -q --tb=short

# Ruff is pinned deliberately. `uvx ruff` resolves to whatever is newest on
# PyPI, so an upstream release could turn CI red with no commit on our side.
# Bump this on purpose, in its own commit, never implicitly.
RUFF := uvx ruff@0.16.3

lint: format-check
	$(RUFF) check .

# Separate target so `local-ci.yml`'s backend-format job has something to call
# and a formatting failure is legible on its own rather than buried in `lint`.
format-check:
	$(RUFF) format --check .

format:
	$(RUFF) check . --fix && $(RUFF) format .

detect-blocking-io:
	@PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run python ../scripts/detect_blocking_io_static.py --output ../.deer-flow/blocking-io-findings.json
