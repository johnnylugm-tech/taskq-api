"""RED acceptance tests for FR-01 task resource CRUD."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from taskq_api.app import app
from taskq_api.api import tasks as task_api
from taskq_api.service import tasks as task_service


@pytest.fixture
def app_client() -> httpx.AsyncClient:
    """Use the ASGI app in-process; no network or external services are used."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


def assert_problem(response: httpx.Response, status_code: int) -> None:
    assert response.status_code == status_code
    assert response.headers["content-type"].startswith("application/problem+json")
    payload = response.json()
    assert payload["status"] == status_code
    assert {"type", "title", "status", "detail", "instance", "correlation_id"} <= payload.keys()


@pytest.mark.asyncio
async def test_fr01_create_rejects_invalid_command_with_422(app_client: httpx.AsyncClient) -> None:
    async with app_client as client:
        response = await client.post(
            "/v1/tasks",
            json={"command": "", "name": "empty-command"},
        )

    assert_problem(response, 422)


@pytest.mark.asyncio
async def test_fr01_duplicate_name_returns_409(app_client: httpx.AsyncClient) -> None:
    payload = {"command": "echo first", "name": "dup-task-name"}
    async with app_client as client:
        first = await client.post("/v1/tasks", json=payload)
        duplicate = await client.post(
            "/v1/tasks",
            json={"command": "echo second", "name": payload["name"]},
        )

    assert first.status_code in (200, 201)
    assert_problem(duplicate, 409)


@pytest.mark.asyncio
async def test_fr01_get_unknown_id_returns_404(app_client: httpx.AsyncClient) -> None:
    unknown_task_id = "00000000-0000-0000-0000-000000000000"
    async with app_client as client:
        response = await client.get(f"/v1/tasks/{unknown_task_id}")

    assert_problem(response, 404)


@pytest.mark.asyncio
async def test_fr01_list_paginates_by_cursor_not_offset(app_client: httpx.AsyncClient) -> None:
    async with app_client as client:
        for sequence in range(51):
            created = await client.post(
                "/v1/tasks",
                json={"command": f"echo {sequence}", "name": f"cursor-task-{sequence}"},
            )
            assert created.status_code in (200, 201)

        first_page = await client.get("/v1/tasks", params={"limit": 50})
        assert first_page.status_code == 200
        first_payload: dict[str, Any] = first_page.json()
        assert len(first_payload["items"]) == 50
        cursor = first_payload.get("next_cursor")
        assert cursor and not str(cursor).isdigit()
        assert "offset" not in first_payload

        second_page = await client.get("/v1/tasks", params={"cursor": cursor, "limit": 50})
        assert second_page.status_code == 200
        second_payload: dict[str, Any] = second_page.json()

    first_ids = {item["id"] for item in first_payload["items"]}
    second_ids = {item["id"] for item in second_payload["items"]}
    assert first_ids.isdisjoint(second_ids)
    assert len(second_payload["items"]) == 1


@pytest.mark.asyncio
async def test_fr01_limit_defaults_50_and_rejects_over_200(app_client: httpx.AsyncClient) -> None:
    async with app_client as client:
        default_response = await client.get("/v1/tasks")
        over_max_response = await client.get("/v1/tasks", params={"limit": 201})

    assert default_response.status_code == 200
    assert len(default_response.json()["items"]) <= 50
    assert_problem(over_max_response, 422)


@pytest.mark.asyncio
async def test_fr01_crud_chain_end_to_end(app_client: httpx.AsyncClient) -> None:
    payload = {"command": "echo hello", "name": "happy-crud-task"}
    async with app_client as client:
        created_response = await client.post("/v1/tasks", json=payload)
        assert created_response.status_code in (200, 201)
        created = created_response.json()
        task_id = created["id"]
        assert uuid.UUID(str(task_id))
        assert created["command"] == payload["command"]
        assert created["name"] == payload["name"]

        fetched_response = await client.get(f"/v1/tasks/{task_id}")
        assert fetched_response.status_code == 200
        assert fetched_response.json()["id"] == task_id

        listed_response = await client.get("/v1/tasks")
        assert listed_response.status_code == 200
        assert any(item["id"] == task_id for item in listed_response.json()["items"])

        deleted_response = await client.delete(f"/v1/tasks/{task_id}")
        assert deleted_response.status_code in (200, 204)

        missing_response = await client.get(f"/v1/tasks/{task_id}")

    assert_problem(missing_response, 404)
