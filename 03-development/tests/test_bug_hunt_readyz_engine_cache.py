"""Bug-hunt repro: _check_migration_state creates a fresh SQLAlchemy
engine on every call. A repeated /readyz poll accumulates engines +
connection pools until the process runs out of memory / file
descriptors.

RED proof: count the number of distinct Engine instances created
across N calls — must be 1 (cached), not N.
"""
from __future__ import annotations



def test_readyz_caches_engine(monkeypatch, tmp_path):
    db = tmp_path / "readyz.sqlite"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db}")

    from taskq_api import app as _app_module

    # Track every distinct Engine created.
    engines_seen: list[object] = []
    real_create_engine = _app_module.create_engine

    def _tracking_create_engine(url, *args, **kwargs):
        eng = real_create_engine(url, *args, **kwargs)
        engines_seen.append(eng)
        return eng

    monkeypatch.setattr(_app_module, "create_engine", _tracking_create_engine)

    # Hit the probe multiple times.
    for _ in range(5):
        is_at_head, _ = _app_module._check_migration_state()
        # Don't care about the verdict — just that probe runs.

    assert len(engines_seen) == 1, (
        f"_check_migration_state created {len(engines_seen)} engines "
        "instead of caching — k8s probes leak one engine per call"
    )
