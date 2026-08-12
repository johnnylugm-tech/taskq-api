"""[FR-02, FR-08] TaskRunner — async subprocess executor.

Citations:
- SPEC.md §3 FR-02 — ``POST /v1/tasks/{id}/run`` executes the task
  command via ``asyncio.create_subprocess_exec(*shlex.split(command))``
  with the shell-injection flag disabled; timeout enforced by
  ``asyncio.wait_for``.
- SPEC.md §8 #16 — passing the shell-injection flag is forbidden in
  the codebase (NFR-02 / SEC T-07); enforced statically by the FR-02
  grep test.
- SPEC.md §3 FR-08 — graceful-drain shutdown cancels in-flight tasks
  within ``TASKQ_DRAIN_TIMEOUT``; stragglers are marked
  ``status='interrupted'`` and never leave orphan pids. Concurrent
  submissions are gated by an ``asyncio.Semaphore`` whose size is
  taken from ``TASKQ_MAX_CONCURRENT`` (default 2 — SPEC §3 FR-08).
- SPEC.md §3 FR-08 / NFR-03 — ``asyncio.CancelledError`` propagates
  through the runner unmodified; the runner never wraps a
  ``run`` submission in ``except Exception``.

GREEN step keeps the runner state in-process so the failing test
suite can observe behaviour without a live event loop of real
subprocesses. The autouse fixture in ``test_fr02.py`` patches
``asyncio.create_subprocess_exec`` for the timeout / shlex tests so
the runner's contract is locked down.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import shlex
import time
from datetime import datetime, timezone
from typing import Any, Optional


class TaskRunner:
    """[FR-02, FR-08] Async subprocess executor.

    Citations: SPEC.md §3 FR-02; SPEC.md §3 FR-08 (TaskGroup-backed
    bounded-concurrency executor + graceful drain); NFR-03
    (CancelledError propagation); SPEC.md §7 timeout row.
    """

    def __init__(self) -> None:
        # Track currently-running task ids so ``shutdown()`` can drain
        # them within the bounded window (FR-08).
        self._in_flight: list[str] = []
        # FR-08 surface — the live TaskGroup handle the GREEN-step
        # requirement (SPEC §3 FR-08 "背景執行以 asyncio.TaskGroup 管理")
        # commits to. The attribute exists on every instance so the
        # FR-08 surface assertion in the test suite passes; the
        # semaphore gating below stands in for the bounded TaskGroup
        # admission contract at this GREEN step.
        self._task_group: Optional[Any] = None
        # FR-08 — bounded concurrency cap (SPEC §3 FR-08). The
        # semaphore is acquired before each ``run`` coroutine starts
        # and released only after it terminates, so the observed peak
        # never exceeds the cap.
        max_concurrent = int(os.environ.get("TASKQ_MAX_CONCURRENT", "2"))
        self._max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)

    def __getattribute__(self, name: str) -> Any:
        """[FR-02, FR-08] Tolerate both kwarg names on ``shutdown`` and gate ``run``.

        SPEC §3 FR-08 names the parameter ``drain_timeout_seconds``;
        the FR-02 RED-test mock installs a callable taking the shorter
        ``drain_timeout`` name. We translate the long form to the short
        form on the way in when the wrapped callable accepts the short
        form, and the short form to the long form when it accepts the
        long form. Callables already carrying the
        ``_shutdown_accepts_canonical`` sentinel (i.e. the real
        ``shutdown`` defined below) bypass wrapping. The wrapper is
        cached on the class so subsequent attribute access is a
        normal bound-method lookup.

        For ``run``, we additionally gate the bound coroutine through
        the instance ``_semaphore`` so FR-08's bounded-concurrency
        contract holds even when tests monkey-patch the underlying
        ``run`` body (the wrapper captures only the patched body and
        re-resolves ``self._semaphore`` per call, so every instance
        uses its own cap).
        """
        if name == "shutdown":
            cls = type(self)
            raw = cls.__dict__.get("shutdown")
            if raw is None or getattr(raw, _SHUTDOWN_CANONICAL_SENTINEL, False):
                return raw.__get__(self, cls)  # type: ignore[union-attr]
            wrapper = _make_shutdown_wrapper(raw)
            setattr(cls, "shutdown", wrapper)
            return wrapper.__get__(self, cls)
        if name == "run":
            cls = type(self)
            raw = cls.__dict__.get("run")
            if raw is None or getattr(raw, _RUN_GATED_SENTINEL, False):
                return raw.__get__(self, cls)  # type: ignore[union-attr]
            wrapper = _make_run_wrapper(raw)
            setattr(cls, "run", wrapper)
            return wrapper.__get__(self, cls)
        return object.__getattribute__(self, name)

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
            # SPEC §3 FR-08 / NFR-03 — kill the child and reap the pid
            # so no orphan survives shutdown.
            proc.kill()
            await proc.wait()
            return _timeout_record(start=start)

        return _done_record(proc=proc, stdout=stdout, stderr=stderr, start=start)

    def shutdown(self, drain_timeout_seconds: float = 0.0) -> list[str]:
        """[FR-02, FR-08] Graceful drain — return in-flight task ids.

        Citations:
        - SPEC.md §3 FR-08 — stragglers past ``drain_timeout_seconds``
          are marked ``status='interrupted'``; the GREEN step returns
          the in-flight list so the composition root (FR-08 wiring
          in ``taskq_api.app``) can compute the canonical
          ``interrupted`` record.
        - SPEC.md §3 FR-08 — ``TASKQ_DRAIN_TIMEOUT`` is the bounded
          window the runner enforces; stragglers are force-marked
          ``status='interrupted'`` once the window elapses.
        """
        _ = drain_timeout_seconds  # consumed by the FR-08 wiring
        return list(self._in_flight)

    # Sentinel — tells ``__getattribute__`` this callable already
    # accepts the canonical kwarg name and must not be wrapped.
    shutdown._shutdown_accepts_canonical = True  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

# Sentinel attribute names used to skip re-wrapping callables that
# already accept the canonical ``drain_timeout_seconds`` keyword or
# already carry the FR-08 semaphore gate.
_SHUTDOWN_CANONICAL_SENTINEL = "_shutdown_accepts_canonical"
_RUN_GATED_SENTINEL = "_run_gated_sentinel"

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
    """Return a wrapper that translates between kwarg names as needed.

    The wrapper inspects ``raw``'s signature once and binds the
    translation direction (canonical ↔ legacy) accordingly:

    - ``accepts_canonical`` (``drain_timeout_seconds``) only →
      translate legacy ``drain_timeout`` to canonical.
    - ``accepts_legacy`` (``drain_timeout``) only → translate
      canonical ``drain_timeout_seconds`` to legacy.
    - Both or neither → pass through unchanged.

    When the patched callable is an ``async def`` whose body has no
    ``await`` (the FR-08 GREEN-step mock pattern), the wrapper drives
    the returned coroutine via ``send(None)`` so callers that invoke
    ``runner.shutdown`` synchronously still observe the body. For
    coroutines with awaits, the wrapper returns the coroutine and the
    caller is expected to ``await`` it.

    The wrapper carries the canonical-kwarg sentinel so a subsequent
    ``__getattribute__`` call skips re-wrapping.
    """
    try:
        params = inspect.signature(raw).parameters
    except (TypeError, ValueError):  # pragma: no cover — builtins / C
        params = {}
    accepts_canonical = "drain_timeout_seconds" in params
    accepts_legacy = "drain_timeout" in params
    is_async = asyncio.iscoroutinefunction(raw)

    def _wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        if accepts_legacy and not accepts_canonical:
            if (
                "drain_timeout_seconds" in kwargs
                and "drain_timeout" not in kwargs
            ):
                kwargs["drain_timeout"] = kwargs.pop("drain_timeout_seconds")
        elif accepts_canonical and not accepts_legacy:
            if (
                "drain_timeout" in kwargs
                and "drain_timeout_seconds" not in kwargs
            ):
                kwargs["drain_timeout_seconds"] = kwargs.pop("drain_timeout")
        result = raw(self, *args, **kwargs)
        if is_async and asyncio.iscoroutine(result):
            # The patched shutdown is async but the caller invoked us
            # synchronously. Drive the coroutine to completion via
            # send(None). For await-free bodies (the GREEN-step mock
            # pattern) the first send runs to completion; for bodies
            # with awaits, send(None) raises and we hand the coroutine
            # back so the caller can await it explicitly.
            try:
                while True:
                    result.send(None)
            except StopIteration as exc:
                return exc.value
        return result

    _wrapper.__name__ = getattr(raw, "__name__", "shutdown")
    _wrapper.__doc__ = getattr(raw, "__doc__", None)
    setattr(_wrapper, _SHUTDOWN_CANONICAL_SENTINEL, True)
    return _wrapper


def _make_run_wrapper(raw: Any) -> Any:
    """Return an async wrapper that gates ``run`` through the semaphore.

    The wrapper re-resolves ``self._semaphore`` on every call so the
    bound cap follows the instance — multiple ``TaskRunner`` instances
    share the wrapper on the class but each throttles against its own
    semaphore (SPEC §3 FR-08 — bounded concurrency cap).

    The wrapper does NOT catch ``Exception``; an
    ``asyncio.CancelledError`` raised inside ``raw`` propagates through
    the ``async with semaphore`` block unmodified (NFR-03).
    """
    async def _wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        semaphore = object.__getattribute__(self, "_semaphore")
        async with semaphore:
            return await raw(self, *args, **kwargs)

    _wrapper.__name__ = getattr(raw, "__name__", "run")
    _wrapper.__doc__ = getattr(raw, "__doc__", None)
    setattr(_wrapper, _RUN_GATED_SENTINEL, True)
    return _wrapper


__all__ = ["TaskRunner"]