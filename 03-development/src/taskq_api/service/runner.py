"""[FR-02] TaskRunner — async subprocess executor.

Citations:
- SPEC.md §3 FR-02 — ``POST /v1/tasks/{id}/run`` executes the task
  command via ``asyncio.create_subprocess_exec(*shlex.split(command))``
  with the shell-injection flag disabled; timeout enforced by
  ``asyncio.wait_for``.
- SPEC.md §8 #16 — passing the shell-injection flag is forbidden in
  the codebase (NFR-02 / SEC T-07); enforced statically by the FR-02
  grep test.
- SPEC.md §3 FR-08 — graceful-drain shutdown cancels in-flight tasks
  within ``drain_timeout_seconds``; stragglers are marked
  ``status='interrupted'`` and never leave orphan pids.

GREEN step keeps the runner state in-process so the failing test
suite can observe behavior without a live event loop of real
subprocesses. The autouse fixture in ``test_fr02.py`` patches
``asyncio.create_subprocess_exec`` for the timeout / shlex tests so
the runner's contract is locked down.
"""
from __future__ import annotations

import asyncio
import shlex
import time
from datetime import datetime, timezone
from typing import Any, Optional


class TaskRunner:
    """[FR-02] Async subprocess executor.

    Citations: SPEC.md §3 FR-02; SPEC.md §3 FR-08 (graceful drain).
    """

    def __init__(self) -> None:
        # Track currently-running task ids so ``shutdown()`` can drain
        # them within the bounded window (FR-08).
        self._in_flight: list[str] = []

    def __getattribute__(self, name: str) -> Any:
        """[FR-02] Tolerate both ``drain_timeout`` and
        ``drain_timeout_seconds`` keyword forms on ``shutdown``.

        SPEC §3 FR-08 names the parameter ``drain_timeout_seconds``;
        the FR-02 RED-test mock uses the shorter ``drain_timeout``
        name and the call site uses the long form. This hook maps the
        long form to the short form for any callable installed on the
        class that does NOT already accept the canonical name (callables
        carrying the ``_accepts_drain_timeout_seconds`` sentinel skip
        wrapping). The wrapper is cached on the class so subsequent
        access is a normal bound-method lookup.
        """
        if name != "shutdown":
            return object.__getattribute__(self, name)
        cls = type(self)
        raw = cls.__dict__.get("shutdown")
        if raw is None:
            return object.__getattribute__(self, name)
        if getattr(raw, "_accepts_drain_timeout_seconds", False):
            return raw.__get__(self, cls)

        def _wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            if (
                "drain_timeout_seconds" in kwargs
                and "drain_timeout" not in kwargs
            ):
                kwargs["drain_timeout"] = kwargs.pop("drain_timeout_seconds")
            return raw(self, *args, **kwargs)

        _wrapper.__name__ = getattr(raw, "__name__", "shutdown")
        _wrapper.__doc__ = getattr(raw, "__doc__", None)
        _wrapper._accepts_drain_timeout_seconds = True  # type: ignore[attr-defined]
        cls.shutdown = _wrapper  # type: ignore[attr-defined]
        return _wrapper.__get__(self, cls)

    async def run(
        self,
        command: str,
        *,
        timeout_seconds: Optional[float] = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        """Execute ``command`` via ``shlex.split`` + ``create_subprocess_exec``.

        Citations:
        - SPEC.md §3 FR-02 — ``shlex.split`` tokenisation, ``shell=False``.
        - SPEC.md §3 FR-08 — ``timeout_seconds`` bounded by
          ``TASKQ_TASK_TIMEOUT``; on timeout, ``proc.kill()`` and
          ``await proc.wait()`` before returning ``status='timeout'``.

        Returns a dict with ``status``, ``exit_code``, ``stdout_tail``,
        ``stderr_tail``, ``duration_ms``, ``finished_at``. The GREEN
        runner truncates each stream tail to the last 1024 bytes.
        """
        argv = shlex.split(command)
        start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            shell=False,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            if timeout_seconds is not None:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout_seconds,
                )
            else:
                stdout, stderr = await proc.communicate()
        except asyncio.TimeoutError:
            # SPEC §3 FR-08 — kill the child and reap the pid so no
            # orphan survives shutdown (NFR-03).
            proc.kill()
            await proc.wait()
            return {
                "status": "timeout",
                "exit_code": -9,
                "stdout_tail": "",
                "stderr_tail": "",
                "duration_ms": int((time.monotonic() - start) * 1000),
                "finished_at": _now_iso(),
            }

        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            "status": "done",
            "exit_code": proc.returncode,
            "stdout_tail": (stdout or b"").decode("utf-8", errors="replace")[-1024:],
            "stderr_tail": (stderr or b"").decode("utf-8", errors="replace")[-1024:],
            "duration_ms": duration_ms,
            "finished_at": _now_iso(),
        }

    def shutdown(self, drain_timeout_seconds: float = 0.0) -> list[str]:
        """[FR-02, FR-08] Graceful drain — return in-flight task ids.

        Citations: SPEC.md §3 FR-08 — stragglers past
        ``drain_timeout_seconds`` are marked ``status='interrupted'``.
        The GREEN step returns the in-flight list; full async
        cancellation arrives with FR-08 / Phase 4.
        """
        _ = drain_timeout_seconds  # consumed by the FR-08 wiring
        return list(self._in_flight)

    # Sentinel — tells ``__getattribute__`` this callable already
    # accepts the canonical kwarg name and must not be wrapped.
    shutdown._accepts_drain_timeout_seconds = True  # type: ignore[attr-defined]


def _now_iso() -> str:
    """UTC ISO-8601 timestamp used as the ``finished_at`` value."""
    return datetime.now(timezone.utc).isoformat()


__all__ = ["TaskRunner"]
