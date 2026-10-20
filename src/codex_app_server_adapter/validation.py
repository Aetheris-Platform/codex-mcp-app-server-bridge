"""Strict JSON and profile/request validation helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

from .errors import ValidationError

if TYPE_CHECKING:
    from .models import StartRequest


def loads_object(text: str) -> dict[str, object]:
    """Decode one JSON object and reject duplicate keys and non-objects."""

    def pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValidationError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(text, object_pairs_hook=pairs)
    except json.JSONDecodeError as exc:
        raise ValidationError("invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValidationError("request must be a JSON object")
    return cast("dict[str, object]", value)


def canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def validate_start_request(
    request: StartRequest,
    *,
    now: datetime,
    allowed_models: tuple[str, ...],
    allowed_sandboxes: tuple[str, ...],
    allowed_worktree_roots: tuple[str, ...],
) -> str:
    """Validate admission-time bounds and return the normalized working directory."""

    prompt_bytes = request.prompt.encode("utf-8")
    if not prompt_bytes or len(prompt_bytes) > 1_000_000:
        raise ValidationError("prompt must be between 1 and 1000000 UTF-8 bytes")
    if request.model not in allowed_models:
        raise ValidationError("model is not allowed by the operator profile")
    if request.sandbox not in allowed_sandboxes:
        raise ValidationError("sandbox is not allowed by the operator profile")
    cwd = Path(request.cwd)
    if not cwd.is_absolute() or not cwd.is_dir():
        raise ValidationError("cwd must be an existing absolute directory")
    normalized = cwd.resolve(strict=True)
    if not any(_within(normalized, Path(root)) for root in allowed_worktree_roots):
        raise ValidationError("cwd is outside the allowed worktree roots")
    deadline = request.deadline()
    current = now.astimezone(UTC)
    if deadline <= current or deadline > current + timedelta(seconds=3600):
        raise ValidationError("deadline must be in the future and within one hour")
    if _has_symlink_component(cwd):
        raise ValidationError("cwd path components may not be symlinks")
    return str(normalized)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.resolve(strict=True))
    except (ValueError, FileNotFoundError):
        return False
    return True


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        if current.is_symlink():
            return True
    return False
