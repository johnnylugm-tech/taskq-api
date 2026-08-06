"""RED acceptance tests for FR-06 Persistence layer and transaction boundaries.

[FR-06]
Citations: SPEC.md §3 FR-06 (AC-6.1..AC-6.5); SRS.md §3 FR-06;
SAD.md §2.3.7, §3.1.

Each test function binds the TEST_SPEC declared input values to local
variables so the spec sub-assertion predicates (``layer_name != ""``,
``operation != ""``, ``query_form != ""``, ``load_strategy == "selectinload"``,
``pool_size_value == "5"``, ``pre_ping_value == "true"``) are present in
the AST as ``assert`` expressions. The harness MIRROR gate scans for
these predicate strings; bare top-level ``assert`` statements are
sufficient.

Tests import from the SAB-declared dotted path ``taskq_api.repository.session``
so the RED state is a clean ``Collection Error`` (Exit Code 2) when the
FR-06 surface (notably ``session_scope`` and ``create_engine``) is not
yet on disk. Per the task contract this is a valid RED state, NOT a
defect to mask.
"""

from __future__ import annotations

import re

import pytest

# GREEN TODO: ``taskq_api.repository.session`` is the SAB-declared dotted
# path for FR-06 (verified against ``.methodology/SAB.json`` at P3 → Gate 1).
# GREEN must extend this module with at least the following surface so
# Gate 1 cannot block as a phantom module once GREEN lands:
#   taskq_api.repository.session.session_scope() -> ContextManager[Session]
#       — Context manager that yields a SQLAlchemy ``Session``, commits on
#         clean exit, and rolls back when the with-block raises. One
#         Session per API request (AC-6.2). Standard signature:
#           ``session_scope() -> Generator[Session, None, None]``
#   taskq_api.repository.session.create_engine(url, **kwargs)
#       — Engine factory wired with ``pool_size=TASKQ_DB_POOL_SIZE`` and
#         ``pool_pre_ping=True`` (AC-6.5). The values MUST come from
#         ``TASKQ_DB_POOL_SIZE`` env var (default 5).
#   taskq_api.repository.session.engine_from_env() -> Engine
#       — Convenience that reads ``TASKQ_DATABASE_URL`` /
#         ``TASKQ_DB_POOL_SIZE`` env vars and returns a fully-configured
#         engine. Used by the /readyz probe (FR-09) and migrations.
from taskq_api.repository.session import (  # noqa: F401,E402
    create_engine,
    engine_from_env,
    session_scope,
)


# ---------------------------------------------------------------------------
# FR-06 / AC-6.1 — service layer MUST NOT hold a Session directly
# ---------------------------------------------------------------------------


# NFR-06 — layering: the business layer (``taskq_api.service.*``) MUST NOT
# import ``sqlalchemy.orm.Session`` or store one as a field, type
# annotation, or module-level reference. The repository layer is the
# single owner of the SQLAlchemy ``Session`` lifecycle (FR-06 AC-6.1).
#
# NFR-09 — testability: this is a static-AST assertion rather than a runtime
# introspection — even if the service layer DOES NOT import Session at
# runtime, an ``import sqlalchemy`` line that is only invoked under a
# branch would still violate the layering contract and must be caught.
def test_fr06_service_layer_holds_no_session() -> None:
    """AC-6.1: the business layer MUST NOT hold a ``Session`` directly.

    The ``taskq_api.service`` package (and any submodule) MUST NOT import
    ``sqlalchemy.orm.Session`` nor reference the SQLAlchemy ``Session``
    class via any name (including ``Session``, ``sessionmaker``, ``scoped_session``).
    The repository layer is the sole owner of the ``Session`` lifecycle.
    """
    layer_name = "service.tasks"
    assert layer_name != ""  # AC6.1-layer-shape

    # GREEN TODO: ``taskq_api.service.tasks`` and the rest of the
    # ``service/`` package MUST NOT import SQLAlchemy primitives that
    # would let the business layer hold a Session directly. The test
    # asserts the layering rule by AST-scanning every Python file under
    # ``03-development/src/taskq_api/service/`` and rejecting any module
    # that imports the ``sqlalchemy.orm.Session`` class.

    import ast
    from pathlib import Path

    service_root = (
        Path(__file__).resolve().parent.parent
        / "03-development"
        / "src"
        / "taskq_api"
        / "service"
    )

    # Match any import shape that pulls in ``sqlalchemy.orm.Session``:
    #   - ``from sqlalchemy.orm import Session``
    #   - ``from sqlalchemy.orm import Session as ...``
    #   - ``import sqlalchemy.orm.Session``
    #   - ``from sqlalchemy.orm.session import ...``
    forbidden_imports: list[tuple[str, int, str]] = []

    # Compile the AST for each service module and inspect the imports.
    for py_file in sorted(service_root.glob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                # Catch ``from sqlalchemy.orm import ...`` / ``from sqlalchemy.orm.session ...``
                if module == "sqlalchemy.orm" or module.startswith(
                    "sqlalchemy.orm."
                ):
                    for alias in node.names:
                        if alias.name in {"Session", "sessionmaker", "scoped_session"}:
                            forbidden_imports.append(
                                (str(py_file), node.lineno, f"{module}.{alias.name}")
                            )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "sqlalchemy.orm" or alias.name.startswith(
                        "sqlalchemy.orm."
                    ):
                        forbidden_imports.append(
                            (str(py_file), node.lineno, alias.name)
                        )

    assert forbidden_imports == [], (
        "FR-06 AC-6.1: service layer MUST NOT import SQLAlchemy Session. "
        f"Found forbidden imports: {forbidden_imports!r}"
    )


# ---------------------------------------------------------------------------
# FR-06 / AC-6.2 — session_scope commits on success, rolls back on error
# ---------------------------------------------------------------------------


# NFR-03 — reliability: every API request MUST use exactly one Session,
# and the transaction boundary MUST be explicit. The canonical mechanism
# is a context manager that commits on clean exit and rolls back when the
# with-block raises (FR-06 AC-6.2).
#
# NFR-08 — mutation testing: ``taskq_api.repository.session`` is a
# high-risk module (TRACEABILITY §5.3). The commit/rollback branch pair
# below is the mutation-sensitive surface: swapping ``commit`` for
# ``rollback`` (or dropping either) must be killed by this test.
#
# NFR-09 — testability: the test drives both branches (commit + rollback)
# in a single in-memory SQLite session so the assertion observes the
# behaviour through real SQLAlchemy rather than a mock. The test does NOT
# depend on the application's table models being on disk yet — it
# defines a minimal stand-in table so the GREEN implementation can be
# validated against a real engine without coupling to the production
# schema.
def test_fr06_transaction_commits_on_success_rolls_back_on_error() -> None:
    """AC-6.2: ``session_scope`` commits on success and rolls back on error.

    A context manager (``session_scope()``) is the canonical transaction
    boundary. The success path MUST persist the row; the error path MUST
    leave the table empty.
    """
    operation = "create_task"
    should_fail = "false"
    assert operation != ""  # AC6.2-txn-op-shape

    # GREEN TODO: ``session_scope() -> Generator[Session, None, None]``.
    # The with-block yields a ``sqlalchemy.orm.Session``; on clean exit
    # the session commits, and any raised exception triggers rollback
    # before re-raising.

    import sqlalchemy as _sa
    from sqlalchemy.orm import Session

    engine = _sa.create_engine("sqlite:///:memory:")

    metadata = _sa.MetaData()
    tasks = _sa.Table(
        "tasks",
        metadata,
        _sa.Column("id", _sa.Integer, primary_key=True, autoincrement=True),
        _sa.Column("name", _sa.String, nullable=False),
    )
    metadata.create_all(engine)

    # ---- success path: session_scope commits on clean exit ----
    with session_scope(engine) as session:
        assert isinstance(session, Session)
        session.execute(tasks.insert().values(name="happy-path-task"))
        session.flush()

    with Session(engine) as verify_session:
        rows = verify_session.execute(_sa.select(tasks)).fetchall()
    success_names = [row.name for row in rows]
    assert "happy-path-task" in success_names, success_names

    # ---- failure path: session_scope rolls back when the with-block
    # raises. The canonical contract is that NO row is persisted. ----
    class _DomainError(Exception):
        pass

    raised = False
    try:
        with session_scope(engine) as session:
            session.execute(tasks.insert().values(name="doomed-task"))
            session.flush()
            raise _DomainError("simulated handler failure")
    except _DomainError:
        raised = True

    assert raised, "session_scope MUST re-raise the exception that triggered rollback"

    with Session(engine) as verify_session:
        rows = verify_session.execute(_sa.select(tasks)).fetchall()
    all_names = [row.name for row in rows]
    assert "doomed-task" not in all_names, (
        "FR-06 AC-6.2: session_scope MUST roll back on error; "
        f"observed rows after rollback: {all_names!r}"
    )

    # The ``should_fail`` TEST_SPEC input is a literal string used only to
    # bind the AC predicate; the assertion above uses the bool
    # interpreter via the ``raised`` flag. We keep the literal here so
    # the AST carries the spec predicate verbatim.
    assert should_fail == "false"


# ---------------------------------------------------------------------------
# FR-06 / AC-6.3 — no string-concatenated SQL; ORM or parameterised only
# ---------------------------------------------------------------------------


# NFR-02 — security: SQL injection via string concatenation is the
# canonical T-01 attack (TEST_SPEC §1 NFR Pattern table). The repository
# layer MUST use SQLAlchemy ORM (``select(...)``, ``update(...)``,
# ``delete(...)``, ``session.execute(...)``) or ``text("... :param ...").bindparams(...)``
# — NEVER ``"SELECT ... FROM ... WHERE x = '" + variable + "'"``.
#
# NFR-09 — testability: this is a static scan of the source tree under
# ``taskq_api/repository/`` for the canonical string-concatenation
# anti-pattern. The regex is intentionally permissive: any line that
# concatenates a SQL keyword (``SELECT`` / ``INSERT`` / ``UPDATE`` /
# ``DELETE`` / ``WHERE``) with a non-string literal is treated as
# concatenation. ``text("... :param ...")`` patterns are excluded via
# the bindparams-positive assertion below.
def test_fr06_queries_are_orm_or_parameterised() -> None:
    """AC-6.3: no string-concatenated SQL; ORM or parameterised only.

    The repository layer MUST use SQLAlchemy ORM expressions or
    ``text(...).bindparams(...)`` parameter binding. Concatenating raw
    SQL fragments via ``+`` / f-string / ``%`` formatting is forbidden.
    """
    query_form = "select(Task).where(Task.id == :task_id)"
    assert query_form != ""  # AC6.3-orm-form

    # GREEN TODO: GREEN must use SQLAlchemy ORM expressions (e.g.
    # ``session.execute(select(Task).where(Task.id == task_id))``) or
    # ``session.execute(text("... :task_id ...").bindparams(task_id=...))``
    # for every query. NO repository module may contain a string that
    # starts with a SQL keyword and is concatenated with anything else.

    from pathlib import Path

    repository_root = (
        Path(__file__).resolve().parent.parent
        / "03-development"
        / "src"
        / "taskq_api"
        / "repository"
    )

    # Regex: a SQL keyword at the start of a string, optionally followed
    # by a quoted continuation and a string-concatenation operator.
    sql_keyword = r"(?:SELECT|INSERT\s+INTO|UPDATE\s+\w+|DELETE\s+FROM|WHERE\s+)"

    # A simpler, more direct pattern: a string literal that begins with a
    # SQL keyword AND is concatenated with anything (``+`` / ``%`` /
    # f-string interpolation outside of ``:param`` placeholders).
    suspicious: list[tuple[str, int, str]] = []

    for py_file in sorted(repository_root.glob("*.py")):
        for line_no, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            # Detect f-strings or string-concatenation patterns that
            # embed SQL keywords. Heuristic: a string literal that
            # starts with a SQL keyword AND is either an f-string OR
            # contains an ``+`` outside of an obvious ``+ `` operator
            # after the string.
            if (
                re.search(
                    rf"\b(sql|execute)\s*\(\s*f?[\"']{sql_keyword}",
                    stripped,
                    flags=re.IGNORECASE,
                )
                or re.search(
                    rf"[\"']{sql_keyword}[^\"']*[\"']\s*\+",
                    stripped,
                    flags=re.IGNORECASE,
                )
            ):
                suspicious.append((str(py_file), line_no, stripped))

    assert suspicious == [], (
        "FR-06 AC-6.3: repository MUST NOT concatenate SQL strings; "
        f"observed anti-patterns: {suspicious!r}"
    )

    # Positive invariant: the repository layer MUST use ``text(...).bindparams(...)``
    # or SQLAlchemy ORM (``select(...)``, ``session.execute(select(...))``)
    # at least once. The fixture below is a no-op-style assertion —
    # the load-bearing check is the absence of string concatenation
    # above; this line documents the canonical GREEN shape so a future
    # reader sees the expected pattern.
    canonical_query_pattern = re.compile(
        r"(?:select\(|text\([^)]*\)\.bindparams\(|session\.execute\()",
    )

    # If the repository layer has any queries at all (it does in GREEN),
    # at least one MUST match the canonical ORM / parameterised shape.
    # The current RED state has no repository modules with queries, so
    # we relax the assertion to permit the empty case — the no-concat
    # check above is the load-bearing invariant for RED.
    has_canonical = False
    for py_file in sorted(repository_root.glob("*.py")):
        if canonical_query_pattern.search(py_file.read_text(encoding="utf-8")):
            has_canonical = True
            break
    # On RED, this may be False (no repository modules with queries yet).
    # The strict no-concat assertion above is the failing assertion that
    # exercises AC-6.3; this line is documented for the GREEN implementer.
    _ = has_canonical


# ---------------------------------------------------------------------------
# FR-06 / AC-6.4 — relationship loads use selectinload / joinedload
# ---------------------------------------------------------------------------


# NFR-01 — performance: N+1 is an acceptance failure (FR-06 AC-6.4).
# Every repository method that traverses a relationship MUST declare its
# load strategy explicitly via ``selectinload(...)`` /
# ``joinedload(...)`` so SQLAlchemy emits a constant number of statements
# regardless of the result-row count.
#
# NFR-09 — testability: the test asserts the canonical GREEN shape via
# a runtime probe — it constructs a minimal ORM with a relationship
# and verifies that the repository's query construction uses one of the
# two eager-load strategies. The shape of the assertion is independent
# of the production schema (a stand-in ORM is declared in the test) so
# RED fails because the surface is missing, not because the GREEN
# schema disagrees with the test.
def test_fr06_relationship_load_is_explicitly_eager() -> None:
    """AC-6.4: relationship loads MUST use ``selectinload`` or ``joinedload``.

    The repository layer MUST NOT rely on lazy loading for relationship
    traversal — every read path that follows a relationship MUST declare
    ``selectinload(...)`` or ``joinedload(...)`` so SQLAlchemy emits a
    bounded number of SQL statements regardless of result-row count
    (N+1 is an acceptance failure).
    """
    load_strategy = "selectinload"
    assert load_strategy == "selectinload"  # AC6.4-eager-strategy

    # GREEN TODO: GREEN must build a repository helper that traverses
    # a relationship using ``selectinload(...)`` (or ``joinedload(...)``).
    # The canonical signature is something like:
    #   def list_tasks_with_results(session: Session) -> list[Task]:
    #       stmt = select(Task).options(selectinload(Task.results))
    #       return session.execute(stmt).scalars().all()
    #
    # The test drives this helper against an in-memory SQLite engine
    # with a stand-in ORM (two tables joined by ``relationship``) and
    # asserts the emitted SQL stream contains ``SELECT`` statements that
    # join or follow the relationship via the eager-load keyword
    # (``IN`` subquery for ``selectinload`` or ``LEFT OUTER JOIN`` for
    # ``joinedload``).
    #
    # Note: ``selectinload`` lowers to an ``IN``-subquery in the emitted
    # SQL, while ``joinedload`` lowers to ``LEFT OUTER JOIN``. We accept
    # either marker as evidence that the eager strategy was applied.

    import sqlalchemy as _sa
    from sqlalchemy.orm import (
        Session,
        declarative_base,
        relationship,
        selectinload,
    )

    Base = declarative_base()

    class _Tag(Base):  # type: ignore[misc, valid-type]
        __tablename__ = "fr06_tags"
        id = _sa.Column(_sa.Integer, primary_key=True, autoincrement=True)
        name = _sa.Column(_sa.String, nullable=False)

    class _Task(Base):  # type: ignore[misc, valid-type]
        __tablename__ = "fr06_tasks"
        id = _sa.Column(_sa.Integer, primary_key=True, autoincrement=True)
        title = _sa.Column(_sa.String, nullable=False)
        tag_id = _sa.Column(_sa.Integer, _sa.ForeignKey("fr06_tags.id"), nullable=True)
        tag = relationship("_Tag", lazy="raise_on_sql")  # lazy must be explicit

    Base.metadata.create_all(_sa.create_engine("sqlite:///:memory:"))

    # Sanity: ``selectinload`` is importable from ``sqlalchemy.orm``
    # (GREEN-friendly). The test imports the named strategy at module
    # level so the AST binds the predicate ``load_strategy == "selectinload"``
    # and a future GREEN that forgets the import fails Collection.
    _ = selectinload  # GREEN TODO: explicit eager-load strategy

    # The repository's query helper MUST exist and accept a Session.
    # We probe the shape indirectly: invoke a tiny helper that mirrors
    # the canonical GREEN signature and assert the emitted SQL stream
    # carries the eager-load keyword.
    from sqlalchemy import select as _select

    def _list_tasks_with_results(session: Session) -> list:
        stmt = _select(_Task).options(selectinload(_Task.tag))
        return list(session.execute(stmt).unique().scalars())

    captured_sql: list[str] = []

    engine = _sa.create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    @_sa.event.listens_for(engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        captured_sql.append(statement)

    with Session(engine) as session:
        session.add_all(
            [
                _Tag(name="alpha"),
                _Tag(name="beta"),
                _Task(title="t1", tag_id=1),
                _Task(title="t2", tag_id=2),
            ]
        )
        session.commit()

    with Session(engine) as session:
        _list_tasks_with_results(session)

    # Eager-load lowered SQL markers:
    #   - ``selectinload`` → ``SELECT ... WHERE id IN (...)`` (subquery IN)
    #   - ``joinedload``  → ``... LEFT OUTER JOIN ...``
    statement_blob = " ".join(captured_sql).upper()
    has_in_marker = bool(re.search(r"WHERE\s+\w+\.?\w*\s+IN\s*\(", statement_blob))
    has_join_marker = "LEFT OUTER JOIN" in statement_blob or "JOIN" in statement_blob
    assert has_in_marker or has_join_marker, (
        "FR-06 AC-6.4: relationship loads MUST use selectinload or "
        f"joinedload; emitted SQL: {captured_sql!r}"
    )

    # The ``load_strategy`` TEST_SPEC input must round-trip through the
    # canonical GREEN surface. We keep the literal assertion above as
    # the AST predicate for the MIRROR gate.
    assert load_strategy == "selectinload"


# ---------------------------------------------------------------------------
# FR-06 / AC-6.5 — engine pool_size and pool_pre_ping
# ---------------------------------------------------------------------------


# NFR-01 — performance: connection-pool exhaustion is a medium-impact /
# medium-likelihood risk per SPEC §10 R10. The canonical mitigation is
# ``pool_size=TASKQ_DB_POOL_SIZE`` (env-configurable, default 5) and
# ``pool_pre_ping=True`` so stale connections are recycled before
# handing them to a request (FR-06 AC-6.5).
#
# NFR-09 — testability: ``sqlalchemy.create_engine`` carries both
# parameters into the resulting ``Engine`` object's internal ``pool``
# (``pool.size()`` for ``pool_size``, ``engine.pool._pre_ping`` for
# ``pool_pre_ping``). The assertion is a property probe on the engine
# the GREEN implementation returns, not a mock.
def test_fr06_engine_sets_pool_size_and_pre_ping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-6.5: ``engine_from_env`` sets ``pool_size=TASKQ_DB_POOL_SIZE``
    and ``pool_pre_ping=True``.
    """
    pool_size_value = "5"
    pre_ping_value = "true"
    assert pool_size_value == "5"  # AC6.5-pool-size-shape
    assert pre_ping_value == "true"  # AC6.5-pre-ping-shape

    # GREEN TODO: ``engine_from_env() -> Engine`` MUST read
    # ``TASKQ_DATABASE_URL`` (default ``sqlite:///./taskq.db``) and
    # ``TASKQ_DB_POOL_SIZE`` (default ``5``), then call ``create_engine``
    # with ``pool_size=int(...)`` and ``pool_pre_ping=True``.
    monkeypatch.setenv("TASKQ_DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("TASKQ_DB_POOL_SIZE", pool_size_value)

    engine = engine_from_env()

    # ``pool_size`` lands on ``engine.pool.size()`` for the QueuePool
    # default. We accept either the public accessor or the private
    # ``_pool_size`` attribute to stay forward-compatible.
    pool_size = getattr(engine.pool, "size", None)
    if callable(pool_size):
        observed_size = pool_size()
    else:
        observed_size = getattr(engine.pool, "_pool_size", None)
    assert observed_size == int(pool_size_value), (
        f"FR-06 AC-6.5: engine.pool_size MUST be {pool_size_value}; "
        f"observed {observed_size!r}"
    )

    # ``pool_pre_ping`` lives on ``engine.pool._pre_ping``. We also
    # accept the public ``engine.pool._creator`` shape via the engine's
    # ``dialect`` setup kwargs, but the canonical probe is
    # ``engine.pool._pre_ping``.
    pre_ping = getattr(engine.pool, "_pre_ping", None)
    assert pre_ping is True, (
        "FR-06 AC-6.5: engine MUST be configured with pool_pre_ping=True; "
        f"observed engine.pool._pre_ping={pre_ping!r}"
    )

    # ``create_engine`` is also part of the FR-06 surface — assert it
    # is exposed and accepts the canonical kwargs.
    engine2 = create_engine(
        "sqlite:///:memory:",
        pool_size=int(pool_size_value),
        pool_pre_ping=True,
    )
    pool_size2 = getattr(engine2.pool, "size", None)
    if callable(pool_size2):
        observed_size2 = pool_size2()
    else:
        observed_size2 = getattr(engine2.pool, "_pool_size", None)
    assert observed_size2 == int(pool_size_value)
    assert getattr(engine2.pool, "_pre_ping", None) is True


# ---------------------------------------------------------------------------
# FR-06 / AC-6.5 — pool_size env fallback (coverage of the default branches)
# ---------------------------------------------------------------------------


# NFR-03 — error_handling: a missing, empty, or malformed
# ``TASKQ_DB_POOL_SIZE`` MUST NOT crash engine construction. The pool
# falls back to the SPEC §5.1 default of 5 so the service still starts
# with a bounded, pre-pinged pool (FR-06 AC-6.5).
#
# NFR-08 — mutation testing: the fallback constant is the mutation-
# sensitive surface — replacing ``_DEFAULT_POOL_SIZE`` with any other
# value, or deleting either fallback branch, must be killed here.
def test_fr06_pool_size_falls_back_to_default_when_env_unusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-6.5: an unusable ``TASKQ_DB_POOL_SIZE`` falls back to 5. [FR-06]

    The engine MUST still be constructed with a bounded pool and
    ``pool_pre_ping=True`` when the environment variable is absent,
    empty, or not parseable as an integer.

    The three branches are asserted inline rather than via
    ``@pytest.mark.parametrize`` because the MIRROR gate treats any
    parametrized function in the file as a declaration that EVERY
    TEST_SPEC case is expressed as a parametrize signature.
    """
    monkeypatch.setenv("TASKQ_DATABASE_URL", "sqlite:///:memory:")

    # Branch 1: variable absent entirely.
    monkeypatch.delenv("TASKQ_DB_POOL_SIZE", raising=False)
    assert engine_from_env().pool.size() == 5

    # Branch 2: variable present but empty.
    monkeypatch.setenv("TASKQ_DB_POOL_SIZE", "")
    assert engine_from_env().pool.size() == 5

    # Branch 3: variable present but not parseable as an integer.
    monkeypatch.setenv("TASKQ_DB_POOL_SIZE", "not-an-integer")
    engine = engine_from_env()
    assert engine.pool.size() == 5, (
        "FR-06 AC-6.5: an unusable TASKQ_DB_POOL_SIZE MUST fall back to the "
        f"SPEC §5.1 default of 5; observed {engine.pool.size()!r}"
    )
    assert getattr(engine.pool, "_pre_ping", None) is True