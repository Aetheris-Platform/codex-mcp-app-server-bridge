"""Strict adapter-owned request and task value models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from .errors import ValidationError

Sandbox = Literal["read-only", "workspace-write"]


class AdapterTaskState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel-requested"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StartRequest(StrictModel):
    request_id: str
    prompt: str = Field(min_length=1)
    cwd: str
    model: str = Field(min_length=1)
    sandbox: Sandbox
    deadline_utc: str

    def request_uuid(self) -> uuid.UUID:
        return _uuid(self.request_id, "request_id")

    def deadline(self) -> datetime:
        return _utc_timestamp(self.deadline_utc, "deadline_utc")


class StatusRequest(StrictModel):
    task_id: str
    wait_seconds: int = Field(default=0, ge=0, le=30)
    cursor: int = Field(default=0, ge=0)

    def task_uuid(self) -> uuid.UUID:
        return _uuid(self.task_id, "task_id")


class ResultRequest(StrictModel):
    task_id: str
    offset_bytes: int = Field(default=0, ge=0)
    limit_bytes: int = Field(default=4096, ge=1, le=4096)

    def task_uuid(self) -> uuid.UUID:
        return _uuid(self.task_id, "task_id")


class CancelRequest(StrictModel):
    task_id: str

    def task_uuid(self) -> uuid.UUID:
        return _uuid(self.task_id, "task_id")


def parse_start(raw: object) -> StartRequest:
    try:
        return StartRequest.model_validate(raw, strict=True)
    except PydanticValidationError as exc:
        raise ValidationError("invalid codex_start request") from exc


def parse_status(raw: object) -> StatusRequest:
    try:
        return StatusRequest.model_validate(raw, strict=True)
    except PydanticValidationError as exc:
        raise ValidationError("invalid codex_status request") from exc


def parse_result(raw: object) -> ResultRequest:
    try:
        return ResultRequest.model_validate(raw, strict=True)
    except PydanticValidationError as exc:
        raise ValidationError("invalid codex_result request") from exc


def parse_cancel(raw: object) -> CancelRequest:
    try:
        return CancelRequest.model_validate(raw, strict=True)
    except PydanticValidationError as exc:
        raise ValidationError("invalid codex_cancel request") from exc


def _uuid(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValidationError(f"{field} must be a UUID") from exc


def _utc_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)
