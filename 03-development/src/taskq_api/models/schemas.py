"""[FR-01] Pydantic request/response schemas for the task resource.

Citations:
- SPEC.md §3 FR-01 (POST body validated by `TaskCreate`; rules: non-empty
  name, ≤1000 chars command, name uniqueness).
- SAD.md §2.4 — `models/schemas.py` is the CRG hub for the models
  community; both `models/orm.py` and tests reference it.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, ConfigDict


class TaskCreate(BaseModel):
    """[FR-01] Request body schema for `POST /v1/tasks`.

    Citations: SPEC.md §3 FR-01 (validation rules) — non-empty `name`,
    `command` ≤ 1000 chars; injection blacklist applied at service
    layer (`service.tasks.TaskService.create`).
    """
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255, description="Unique task name.")
    command: str = Field(..., min_length=1, max_length=1000, description="Shell command to execute.")


class TaskOut(BaseModel):
    """[FR-01] Response body schema for task endpoints.

    Citations: SPEC.md §3 FR-01 (GET single returns full columns).
    """
    id: str
    name: str
    command: str
    status: str = "pending"


__all__ = ["TaskCreate", "TaskOut"]
