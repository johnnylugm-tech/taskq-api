"""[FR-07] Alembic revisions sub-package.

Citations:
- SPEC.md §3 FR-07 — each revision (``v1_initial`` / ``v2_tags`` /
  ``v3_split_results``) defines both ``upgrade()`` and
  ``downgrade()`` with no destructive shortcut.
- SAD.md §3.3 — alembic discovers revisions via this package.
"""

__all__: list[str] = []
