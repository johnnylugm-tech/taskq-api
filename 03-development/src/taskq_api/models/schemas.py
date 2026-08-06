"""Pydantic contracts for task resources.

[FR-01]
Citations: SPEC.md lines 79-91; SRS.md lines 78-86.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaskCreate(BaseModel):
    """Validate task creation input. [FR-01]

    Citations: SPEC.md lines 81, 88.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    command: str = Field(min_length=1, max_length=1000)
    name: str = Field(min_length=1, max_length=255)

    @field_validator("command")
    @classmethod
    def reject_unsafe_command(cls, command: str) -> str:
        """Reject command separators used for injection. [FR-01]

        Citations: SPEC.md line 88.
        """
        if any(character in command for character in (";", "&", "|", "`", "\n", "\r")):
            raise ValueError("command contains prohibited characters")
        return command


class TaskListQuery(BaseModel):
    """Declare bounded cursor-list inputs. [FR-01]

    Citations: SPEC.md lines 90-91.
    """

    DEFAULT_LIMIT: ClassVar[int] = 50
    MAX_LIMIT: ClassVar[int] = 200

    status: str | None = None
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    cursor: str | None = None
