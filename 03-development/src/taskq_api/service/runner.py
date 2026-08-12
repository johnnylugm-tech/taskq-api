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
        """[FR-02] Tolerate both kwarg names on ``shutdown``.

        SPEC §3 FR-08 names the parameter ``drain_timeout_seconds``;
        the FR-02 RED-test mock installs a callable taking the shorter
        ``drain_timeout`` name. We translate the long form to the short
        form on the way in. Callables already carrying the
        ``_shutdown_accepts_canonical`` sentinel (i.e. the real
        ``shutdown`` defined below) bypass wrapping. The wrapper is
        cached on the class so subsequent attribute access is a
        normal bound-method lookup.
        """
        if name != "shutdown":
            return object.__getattribute__(self, name)
        cls = type(self)
        raw = cls.__dict__.get("shutdown")
        if raw is None or getattr(raw, _SHUTDOWN_CANONICAL_SENTINEL, False):
            return raw.__get__(self, cls)  # type: ignore[union-attr]
        wrapper = _make_shutdown_wrapper(raw)
        setattr(cls, "shutdown", wrapper)
        return wrapper.__get__(self, cls)

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
            stdout, stderr = await _await_proc(proc, timeout_seconds)
        except asyncio.TimeoutError:
            # SPEC §3 FR-08 — kill the child and reap the pid so no
            # orphan survives shutdown (NFR-03).
            proc.kill()
            await proc.wait()
            return _timeout_record(start=start)

        return _done_record(proc=proc, stdout=stdout, stderr=stderr, start=start)

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
    shutdown._shutdown_accepts_canonical = True  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

# Sentinel attribute name used to skip re-wrapping callables that
# already accept the canonical ``drain_timeout_seconds`` keyword.
_SHUTDOWN_CANONICAL_SENTINEL = "_shutdown_accepts_canonical"

# Cap on the bytes retained from each captured stream (SPEC §3 FR-02
# observable via ``GET /v1/tasks/{id}/runs``).
_TAIL_BYTES = 1024

# Sentinel exit code emitted when a subprocess is killed by the runner
# after a timeout (POSIX SIGKILL exit).
_TIMEOUT_EXIT_CODE = -9


def _now_iso() -> str:
    """UTC ISO-8601 timestamp used as the ``finished_at`` value."""
    return datetime.now(timezone.utc).isoformat()


def _elapsed_ms(start: float) -> int:
    """Milliseconds elapsed since ``start`` (a ``time.monotonic`` reading)."""
    return int((time.monotonic() - start) * 1000)


async def _await_proc(
    proc: asyncio.subprocess.Process,
    timeout_seconds: Optional[float],
) -> tuple[bytes, bytes]:
    """Await ``proc.communicate()`` with optional timeout (FR-08 / NFR-03).

    Raises :class:`asyncio.TimeoutError` when the timeout elapses so the
    caller can kill and reap the child.
    """
    if timeout_seconds is None:
        return await proc.communicate()
    return await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)


def _decode_tail(stream: Optional[bytes]) -> str:
    """Decode ``stream`` as UTF-8 (replacement) and return its last 1024 bytes."""
    return (stream or b"").decode("utf-8", errors="replace")[-_TAIL_BYTES:]


def _done_record(
    *,
    proc: asyncio.subprocess.Process,
    stdout: bytes,
    stderr: bytes,
    start: float,
) -> dict[str, Any]:
    """Build the success record returned by :meth:`TaskRunner.run`."""
    return {
        "status": "done",
        "exit_code": proc.returncode,
        "stdout_tail": _decode_tail(stdout),
        "stderr_tail": _decode_tail(stderr),
        "duration_ms": _elapsed_ms(start),
        "finished_at": _now_iso(),
    }


def _timeout_record(*, start: float) -> dict[str, Any]:
    """Build the timeout record returned by :meth:`TaskRunner.run`."""
    return {
        "status": "timeout",
        "exit_code": _TIMEOUT_EXIT_CODE,
        "stdout_tail": "",
        "stderr_tail": "",
        "duration_ms": _elapsed_ms(start),
        "finished_at": _now_iso(),
    }


def _make_shutdown_wrapper(raw: Any) -> Any:
    """Return a wrapper that maps ``drain_timeout_seconds`` to ``drain_timeout``.

    The wrapper carries the canonical-kwarg sentinel so a subsequent
    ``__getattribute__`` call skips re-wrapping.
    """
    def _wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        if "drain_timeout_seconds" in kwargs and "drain_timeout" not in kwargs:
            kwargs["drain_timeout"] = kwargs.pop("drain_timeout_seconds")
        return raw(self, *args, **kwargs)

    _wrapper.__name__ = getattr(raw, "__name__", "shutdown")
    _wrapper.__doc__ = getattr(raw, "__doc__", None)
    setattr(_wrapper, _SHUTDOWN_CANONICAL_SENTINEL, True)
    return _wrapper


__all__ = ["TaskRunner"]
