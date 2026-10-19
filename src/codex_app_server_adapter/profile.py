"""Operator-owned runtime and filesystem profile."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from .errors import ValidationError
from .validation import loads_object


@dataclass(frozen=True, slots=True)
class OperatorProfile:
    runtime_path: Path
    runtime_version: str
    runtime_sha256: str
    socket_path: Path
    allowed_worktree_roots: tuple[Path, ...]
    allowed_models: tuple[str, ...]
    allowed_sandboxes: tuple[str, ...]
    data_root: Path

    def validate(self) -> None:
        """Fail closed if runtime, socket, or storage is not private and exact."""

        runtime = self.runtime_path
        if (
            not runtime.is_absolute()
            or _has_symlink_component(runtime)
            or runtime.is_symlink()
            or not runtime.is_file()
        ):
            raise ValidationError("runtime_path must be an existing non-symlink file")
        if not os.access(runtime, os.X_OK):
            raise ValidationError("runtime_path must be executable")
        if len(self.runtime_version) == 0 or len(self.runtime_sha256) != 64:
            raise ValidationError("runtime identity is incomplete")
        try:
            int(self.runtime_sha256, 16)
        except ValueError as exc:
            raise ValidationError("runtime_sha256 must be hexadecimal") from exc
        if (
            not self.allowed_worktree_roots
            or not self.allowed_models
            or any(not value for value in self.allowed_models)
        ):
            raise ValidationError("profile allowlists must be nonempty")
        if not self.allowed_sandboxes or not set(self.allowed_sandboxes) <= {
            "read-only",
            "workspace-write",
        }:
            raise ValidationError("profile contains an unsupported sandbox")
        for root in self.allowed_worktree_roots:
            if (
                not root.is_absolute()
                or _has_symlink_component(root)
                or not root.is_dir()
                or root.is_symlink()
            ):
                raise ValidationError("allowed worktree roots must be real directories")
        socket_parent = self.socket_path.parent
        if (
            not self.socket_path.is_absolute()
            or _has_symlink_component(socket_parent)
            or not socket_parent.is_dir()
            or socket_parent.is_symlink()
        ):
            raise ValidationError("socket parent must be an existing directory")
        _require_private(socket_parent, "socket parent")
        if self.socket_path.exists() and not stat.S_ISSOCK(self.socket_path.stat().st_mode):
            raise ValidationError("socket path is not a Unix socket")
        if not self.data_root.is_absolute() or _has_symlink_component(self.data_root):
            raise ValidationError("data_root must be an absolute non-symlink path")
        if self.data_root.exists():
            if not self.data_root.is_dir() or self.data_root.is_symlink():
                raise ValidationError("data_root must be a real directory")
            _require_private(self.data_root, "data root")

    def verify_runtime(self) -> None:
        self.validate()
        digest = hashlib.sha256(self.runtime_path.read_bytes()).hexdigest()
        if digest != self.runtime_sha256.lower():
            raise ValidationError("runtime digest does not match the operator profile")
        try:
            result = subprocess.run(  # noqa: S603 - path is digest-verified immediately above
                [str(self.runtime_path), "--version"],
                capture_output=True,
                check=False,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValidationError("runtime version probe failed") from exc
        reported = (result.stdout or "").strip()
        if result.returncode != 0 or reported != self.runtime_version:
            raise ValidationError("runtime version does not match the operator profile")

    def identity_digest(self) -> str:
        """Return the digest of the complete normalized operator profile."""

        value = {
            "runtime_path": str(self.runtime_path),
            "runtime_version": self.runtime_version,
            "runtime_sha256": self.runtime_sha256.lower(),
            "socket_path": str(self.socket_path),
            "allowed_worktree_roots": [str(path) for path in self.allowed_worktree_roots],
            "allowed_models": list(self.allowed_models),
            "allowed_sandboxes": list(self.allowed_sandboxes),
            "data_root": str(self.data_root),
        }
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            )
        ).hexdigest()

    @classmethod
    def from_json(cls, text: str) -> OperatorProfile:
        """Parse one strict, duplicate-key-free operator profile object."""

        profile = cls.parse_json(text)
        profile.verify_runtime()
        return profile

    @classmethod
    def parse_json(cls, text: str) -> OperatorProfile:
        """Validate configuration without invoking the runtime before ownership."""

        value = loads_object(text)
        expected = {
            "runtime_path",
            "runtime_version",
            "runtime_sha256",
            "socket_path",
            "allowed_worktree_roots",
            "allowed_models",
            "allowed_sandboxes",
            "data_root",
        }
        if set(value) != expected:
            raise ValidationError("profile fields are incomplete or unexpected")
        roots = value["allowed_worktree_roots"]
        models = value["allowed_models"]
        sandboxes = value["allowed_sandboxes"]
        if not all(isinstance(item, str) for item in (value["runtime_path"], value["runtime_version"], value["runtime_sha256"], value["socket_path"], value["data_root"])):
            raise ValidationError("profile scalar field has the wrong type")
        if not isinstance(roots, list) or not all(isinstance(item, str) for item in roots):
            raise ValidationError("profile roots have the wrong type")
        if not isinstance(models, list) or not all(isinstance(item, str) for item in models):
            raise ValidationError("profile models have the wrong type")
        if not isinstance(sandboxes, list) or not all(isinstance(item, str) for item in sandboxes):
            raise ValidationError("profile sandboxes have the wrong type")
        profile = cls(
            runtime_path=Path(cast("str", value["runtime_path"])),
            runtime_version=cast("str", value["runtime_version"]),
            runtime_sha256=cast("str", value["runtime_sha256"]),
            socket_path=Path(cast("str", value["socket_path"])),
            allowed_worktree_roots=tuple(Path(item) for item in roots),
            allowed_models=tuple(models),
            allowed_sandboxes=tuple(sandboxes),
            data_root=Path(cast("str", value["data_root"])),
        )
        profile.validate()
        return profile


def _require_private(path: Path, label: str) -> None:
    mode = path.stat().st_mode
    if path.stat().st_uid != os.getuid() or mode & 0o077:
        raise ValidationError(f"{label} must be owned by the current user with mode 0700")


def _has_symlink_component(path: Path) -> bool:
    """Reject aliases in every existing component of an absolute path."""

    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        if current.is_symlink():
            return True
    return False
