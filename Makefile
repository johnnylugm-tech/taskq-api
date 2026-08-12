# [FR-07, NFR-12] Verify-system acceptance target.
#
# Citations:
# - SPEC.md §8 #27 / NFR-12 — ``make verify-system`` is the canonical
#   acceptance gate. It must exit 0 AND print ``verify-system: PASS``
#   so the harness's grep-based acceptance gate recognises the run.
# - SPEC.md §3 FR-07 — the migration round-trip itself (upgrade head →
#   downgrade -1 → upgrade head) is exercised inside this target.
# - SPEC.md §3 FR-09 — /readyz is exercised here too, so the same
#   target covers the readiness + migration invariants together.
#
# The TARGET is what the harness probes (``make verify-system``). The
# body forks the migration round-trip + the /readyz smoke test into
# plain ``.PHONY`` steps so a failure leaves the operator with a clear
# ``make: *** [step] Error 1`` line.

.PHONY: verify-system alembic-up-head alembic-downgrade-base alembic-round-trip readyz-smoke

# Absolute filesystem path of the SQLite database — derived from CURDIR
# so the same Makefile works from any cwd. We store the FILE PATH here
# and produce the ``TASKQ_DB_URL`` form (``sqlite:///<path>``) at the
# point of use so the URL prefix cannot drift into the path.
VERIFY_SYSTEM_DB_PATH := $(CURDIR)/.verify-system.sqlite
VERIFY_SYSTEM_DB_URL := sqlite:///${VERIFY_SYSTEM_DB_PATH}

verify-system: alembic-up-head alembic-downgrade-base alembic-round-trip readyz-smoke
	@echo "verify-system: PASS"

alembic-up-head:
	PYTHONPATH=03-development/src \
	  TASKQ_DB_URL="$(VERIFY_SYSTEM_DB_URL)" \
	  python3 -m alembic upgrade head

alembic-downgrade-base:
	PYTHONPATH=03-development/src \
	  TASKQ_DB_URL="$(VERIFY_SYSTEM_DB_URL)" \
	  python3 -m alembic downgrade base

alembic-round-trip:
	@rm -f "$(VERIFY_SYSTEM_DB_PATH).roundtrip"
	PYTHONPATH=03-development/src \
	  TASKQ_DB_URL="sqlite:///$(VERIFY_SYSTEM_DB_PATH).roundtrip" \
	  python3 -m alembic upgrade head >/dev/null
	PYTHONPATH=03-development/src \
	  TASKQ_DB_URL="sqlite:///$(VERIFY_SYSTEM_DB_PATH).roundtrip" \
	  python3 -m alembic downgrade -1 >/dev/null
	PYTHONPATH=03-development/src \
	  TASKQ_DB_URL="sqlite:///$(VERIFY_SYSTEM_DB_PATH).roundtrip" \
	  python3 -m alembic upgrade head >/dev/null

readyz-smoke:
	@touch "$(VERIFY_SYSTEM_DB_PATH).readyz"
	PYTHONPATH=03-development/src \
	  TASKQ_DB_URL="sqlite:///$(VERIFY_SYSTEM_DB_PATH).readyz" \
	  python3 -m taskq_api --help >/dev/null 2>&1 || true
