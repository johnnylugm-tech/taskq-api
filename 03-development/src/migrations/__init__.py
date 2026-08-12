"""[FR-07] Alembic migration package.

Citations:
- SPEC.md §3 FR-07 — three Alembic revisions, each with a working
  ``downgrade()`` (no destructive shortcut such as
  ``op.execute("DROP TABLE ...")``).
- SAD.md §3.3 — schema migrations live under
  ``migrations/versions/`` and are driven by alembic's offline
  SQL generator at test time.
"""

__all__: list[str] = []
