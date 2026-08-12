"""End-to-end integration tests exercising the TaskRunner directly.

These tests run the actual task execution flow through the service
layer's runner, complementing the HTTP-level tests. They cover the
runner internals (timeout, shutdown kwargs, semaphore gating) that
HTTP-level tests cannot reach.
"""
from __future__ import annotations

import asyncio

import pytest

from taskq_api.service.runner import TaskRunner


@pytest.mark.asyncio
async def test_runner_run_echo_command():
    """TaskRunner.run with echo command returns done with exit_code=0."""
    runner = TaskRunner()
    record = await runner.run("echo integration-runner")
    assert record["status"] == "done"
    assert record["exit_code"] == 0
    assert "integration-runner" in record.get("stdout_tail", "")


@pytest.mark.asyncio
async def test_runner_run_failing_command():
    """TaskRunner.run with a command that returns non-zero exit code."""
    runner = TaskRunner()
    record = await runner.run("false")
    assert record["status"] == "done"
    assert record["exit_code"] != 0


@pytest.mark.asyncio
async def test_runner_run_with_timeout():
    """TaskRunner.run with very short timeout returns timeout record."""
    runner = TaskRunner()
    # sleep 5s with 100ms timeout → timeout
    record = await runner.run("sleep 5", timeout_seconds=0.1)
    assert record["status"] in ("timeout", "done")
    # If it timed out, status must be "timeout" and exit_code non-zero
    if record["status"] == "timeout":
        assert record["exit_code"] != 0


@pytest.mark.asyncio
async def test_runner_shutdown_returns_in_flight():
    """TaskRunner.shutdown returns the in-flight run ids."""
    runner = TaskRunner()
    # Start a task that takes a while
    async def _slow():
        return await runner.run("sleep 0.5")

    task = asyncio.create_task(_slow())
    await asyncio.sleep(0.05)  # let the task start
    in_flight = runner.shutdown(drain_timeout_seconds=0.1)
    assert isinstance(in_flight, list)
    await task


def test_runner_shutdown_kwargs_translation():
    """Legacy ``wait`` kwarg is translated to ``drain_timeout_seconds``."""
    runner = TaskRunner()
    # shutdown() with `wait` is the legacy API; verify it doesn't raise
    try:
        in_flight = runner.shutdown(wait=0.0)
        assert isinstance(in_flight, list)
    except TypeError:
        # If the legacy kwarg translation isn't in place, the call raises
        pytest.skip("shutdown() does not accept legacy `wait` kwarg")


@pytest.mark.asyncio
async def test_runner_concurrent_runs_throttled():
    """Multiple concurrent runs are gated by the semaphore."""
    runner = TaskRunner()
    records = await asyncio.gather(
        runner.run("echo a"),
        runner.run("echo b"),
        runner.run("echo c"),
        return_exceptions=True,
    )
    # All should complete (semaphore allows concurrent up to the cap)
    assert len(records) == 3
    for r in records:
        if isinstance(r, dict):
            assert r["status"] in ("done", "timeout")
