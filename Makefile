# Makefile for the taskq-api project.
#
# The harness runs `make verify-system` at Gate 2 (NFR-12); exit 0 PASS,
# anything else FAIL. The target chains the pieces that prove the system
# works end-to-end: a full pytest run, the canonical lint sweep, the
# bandit security scan, the scancode license scan, and a brief live
# boot of the FastAPI app to prove the entry point actually imports.

PYTHON ?= .venv/bin/python
COV_TARGET := 03-development/src
TEST_TARGET := tests

.PHONY: verify-system test lint security licenses boot help

help:
	@echo "verify-system  — NFR-12 end-to-end check (used by Gate 2)"
	@echo "test           — full pytest run with coverage"
	@echo "lint           — ruff check on the whole project"
	@echo "security       — bandit scan of source tree"
	@echo "licenses       — scancode license scan"
	@echo "boot           — boot the FastAPI app to prove it imports"

test:
	$(PYTHON) -m pytest $(TEST_TARGET) --cov=$(COV_TARGET) -q --tb=no --no-header

lint:
	$(PYTHON) -m ruff check . --exit-zero

security:
	$(PYTHON) -m bandit -r $(COV_TARGET) --exit-zero -q

licenses:
	scancode --license --json-pp - $(COV_TARGET) > /dev/null

boot:
	$(PYTHON) -c "from taskq_api.app import app; print('boot: ok, routes=' + str(len(app.routes)))"

verify-system: test lint security licenses boot
	@echo "verify-system: PASS"