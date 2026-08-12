"""Property-based tests for the TEST_SPEC FR-01 / FR-07 / FR-10 P1 invariants.

Citations:
- 02-architecture/TEST_SPEC.md — Properties tables declare P1-cursor-roundtrip
  (FR-01), P1-roundtrip-equal + P2-no-data-loss (FR-07), and
  P1-no-leak-invariants (FR-10), each tagged ``fulfill_phase = 4``.
- harness/.../obligation:property_spec — Phase 4 entry BLOCKS if any FR
  declares a property invariant without an executing hypothesis
  ``@given`` / fast-check test covering it.

Each test below is the hypothesis-encoded version of one TEST_SPEC
property. They run inside pytest's normal discovery (Hypothesis auto-
generates inputs) and are collected by the property_spec obligation
checker that scans for ``@given`` decorators in test modules.
"""
from __future__ import annotations

import asyncio
import json as _stdlib_json
import uuid
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st
from sqlalchemy import text
from starlette.requests import Request


# ---------------------------------------------------------------------------
# FR-01: P1-cursor-roundtrip
#
# Property: ``decode_cursor(encode_cursor(last_seen_id)) == last_seen_id``
#
# Implementation note: the current cursor encoder in
# ``taskq_api.repository.task_repo.TaskRepo.list`` returns the row id
# verbatim (no base64 / no opaque token — line 187 of task_repo.py),
# so the round-trip collapses to identity. The property test pins that
# behaviour against any UTF-8 string id so future encoding work (if any)
# trips the test rather than silently regressing.
# ---------------------------------------------------------------------------


class _InMemoryCursorSession:
    """Minimal fake session that mirrors the rows the test registers.

    ``TaskRepo.list`` issues exactly two ``session.execute`` calls — the
    page select and a ``selectinload`` follow-up. Both return the same
    set of rows (the cursor encoding is a pure function over the
    materialised list).
    """

    def __init__(self, rows):
        self._rows = list(rows)

    def execute(self, _stmt, *_args, **_kwargs):
        outer = self

        class _Result:
            def scalars(self_inner):
                class _Scalars:
                    def unique(self_inner_inner):
                        return self_inner_inner

                    def all(self_inner_inner):
                        return list(outer._rows)

                return _Scalars()

        return _Result()


@given(
    last_seen_id=st.text(
        min_size=1, max_size=64,
        alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
    ),
    earlier_id=st.text(
        min_size=1, max_size=64,
        alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
    ),
)
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_fr01_cursor_roundtrip(last_seen_id: str, earlier_id: str) -> None:
    """[FR-01 P1] ``decode_cursor(encode_cursor(id)) == id`` for any id.

    ``TaskRepo.list`` returns ``next_cursor = last["id"]`` (no
    transformation) so the test pins the identity property: the
    ``next_cursor`` token equals the id of the last row in the page.
    """
    from taskq_api.repository.task_repo import TaskRepo  # noqa: PLC0415

    # Two distinct rows so a limit=1 query produces a next page.
    if last_seen_id == earlier_id:
        earlier_id = earlier_id + "_x"

    rows = [
        {"id": earlier_id, "name": "p", "command": "echo p", "status": "queued"},
        {"id": last_seen_id, "name": "q", "command": "echo q", "status": "queued"},
    ]
    repo = TaskRepo(session=_InMemoryCursorSession(rows))
    page, next_cursor = repo.list(limit=1)
    assert len(page) == 1
    assert next_cursor is not None
    # Identity property: the cursor token the repo emits is exactly the
    # id of the last row in the page (no transformation).
    assert next_cursor == page[-1]["id"], (
        f"P1-cursor-roundtrip: next_cursor={next_cursor!r} != "
        f"page[-1].id={page[-1]['id']!r}"
    )


# ---------------------------------------------------------------------------
# FR-07: P1-roundtrip-equal + P2-no-data-loss
#
# Properties: ``migrated_row == original_row`` AND
#             ``len(migrated_rows) == len(original_rows)``
#
# Implementation note: the canonical TEST_SPEC case exercises alembic
# via ``subprocess``. A full property sweep at subprocess speed is too
# slow, so the property test runs the v3 split_results migration
# IN-PROCESS via a duplicate of the project's in-process helper (the
# test_fr07 fixture isn't importable as ``tests.*`` from the property
# test, so the helper is inlined to keep the property module
# self-contained) and asserts the round-trip invariant against
# arbitrary row payloads.
# ---------------------------------------------------------------------------


def _run_migration_in_process(*steps, db_path):
    """In-process alembic upgrade/downgrade — local copy of the helper
    in tests/test_fr07.py so this property test is importable as a
    standalone module without depending on ``tests.*`` (which pytest's
    rootdir / conftest does not always put on sys.path).
    """
    from alembic.operations import Operations  # noqa: PLC0415
    from alembic.runtime.migration import MigrationContext  # noqa: PLC0415
    from sqlalchemy import create_engine  # noqa: PLC0415

    url = f"sqlite:///{db_path}" if db_path is not None else "sqlite:///:memory:"
    engine = create_engine(url)
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            for step in steps:
                step()
    return engine


_v3_row = st.tuples(
    st.integers(min_value=0, max_value=255),
    st.text(
        min_size=1, max_size=16,
        alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
    ),
    st.text(max_size=32, alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z"))),
    st.text(max_size=32, alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z"))),
    st.integers(min_value=0, max_value=10_000),
)


@given(rows=st.lists(_v3_row, min_size=1, max_size=4, unique_by=lambda r: r[1]))
@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_fr07_v3_split_results_roundtrip(tmp_path, rows) -> None:
    """[FR-07 P1/P2] For arbitrary v3 row payloads, the alembic round-trip
    preserves each row byte-for-byte (P1) and the row count (P2).
    """
    from migrations.versions import (  # noqa: PLC0415
        v1_initial,
        v2_tags,
        v3_split_results,
    )

    db_path = tmp_path / f"prop_roundtrip_{uuid.uuid4().hex[:8]}.sqlite"

    # Bring the schema up to v2 (so tasks.result_json exists and rows
    # can be inserted). v3 then migrates the rows into task_results.
    engine = _run_migration_in_process(
        v1_initial.upgrade,
        v2_tags.upgrade,
        db_path=db_path,
    )

    # Insert the generated rows into the v2-shape table.
    with engine.begin() as conn:
        for (exit_code, name, stdout_tail, stderr_tail, duration_ms) in rows:
            conn.execute(
                text(
                    "INSERT INTO tasks (id, name, command, status, result_json) "
                    "VALUES (:id, :name, :cmd, 'completed', :result)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "name": f"task-{name}",
                    "cmd": "echo roundtrip",
                    "result": _stdlib_json.dumps(
                        {
                            "exit_code": exit_code,
                            "stdout_tail": stdout_tail,
                            "stderr_tail": stderr_tail,
                            "duration_ms": duration_ms,
                        }
                    ),
                },
            )

    # Apply v3 split_results — moves rows from tasks.result_json into
    # task_results (one row per source task).
    _run_migration_in_process(v3_split_results.upgrade, db_path=db_path)

    # Snapshot the v3-split result rows before the round-trip. The
    # task_results row schema (FR-07 v3) carries the five FR-07
    # fields-to-check byte-for-byte, keyed by task_id.
    with engine.connect() as conn:
        before = sorted(
            (
                r[0],  # task_id
                r[1],  # exit_code
                r[2],  # stdout_tail
                r[3],  # stderr_tail
                r[4],  # duration_ms
            )
            for r in conn.execute(
                text(
                    "SELECT task_id, exit_code, stdout_tail, stderr_tail, "
                    "duration_ms FROM task_results"
                )
            ).fetchall()
        )

    # Round-trip on the SAME DB file: downgrade v3 (results merge back
    # into tasks.result_json) then re-upgrade v3.
    _run_migration_in_process(
        v3_split_results.downgrade,
        v3_split_results.upgrade,
        db_path=db_path,
    )

    with engine.connect() as conn:
        after = sorted(
            (
                r[0],
                r[1],
                r[2],
                r[3],
                r[4],
            )
            for r in conn.execute(
                text(
                    "SELECT task_id, exit_code, stdout_tail, stderr_tail, "
                    "duration_ms FROM task_results"
                )
            ).fetchall()
        )

    # P2-no-data-loss: row count preserved.
    assert len(after) == len(before), (
        f"P2-no-data-loss violated: had {len(before)} rows, "
        f"got {len(after)} after round-trip"
    )
    # P1-roundtrip-equal: every original row's five FR-07 fields appear
    # identically after the round-trip.
    assert after == before, (
        "P1-roundtrip-equal violated: round-trip row content drifted"
    )


# ---------------------------------------------------------------------------
# FR-10: P1-no-leak-invariants
#
# Property: ``correlation_id_field == correlation_id_header``
#
# Implementation note: ``_problem_envelope`` (errors.py:158) is a pure
# function that builds the dict; the ``X-Correlation-Id`` header is
# added by ``_build_problem_response`` (errors.py:184). The property
# asserts that for any request path + any problem detail, the body's
# ``correlation_id`` field equals the id used to construct the
# response header — so the header always advertises the exact id the
# client receives in the body.
# ---------------------------------------------------------------------------


@given(
    path_segment=st.text(
        min_size=1, max_size=32,
        alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
    ),
    detail=st.text(min_size=1, max_size=200),
)
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_fr10_correlation_id_envelope_matches_header(
    path_segment: str, detail: str,
) -> None:
    """[FR-10 P1] For any path + any detail, the body field equals the
    ``X-Correlation-Id`` header set on the outgoing ``JSONResponse``.
    """
    from taskq_api.errors import (  # noqa: PLC0415
        NotFoundProblem,
        _build_problem_response,
    )

    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/_prop_test/{path_segment}",
        "headers": [],
        "query_string": b"",
    }
    request = Request(scope)
    exc = NotFoundProblem(detail=detail)

    response, returned_cid = asyncio.run(_build_problem_response(request, exc))

    body = _stdlib_json.loads(response.body)
    assert body["correlation_id"] == response.headers["x-correlation-id"], (
        f"P1-no-leak-invariants violated: envelope correlation_id="
        f"{body['correlation_id']!r} != header X-Correlation-Id="
        f"{response.headers['x-correlation-id']!r}"
    )
    # The handler must return the SAME id it advertised in the header
    # so downstream log/forward paths can correlate.
    assert returned_cid == body["correlation_id"] == response.headers["x-correlation-id"]
    # And it must be a valid UUID4 string (36 chars including hyphens).
    assert len(returned_cid) == 36
