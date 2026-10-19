"""Errors raised by the developer-only adapter."""

from __future__ import annotations


class AdapterError(Exception):
    """Base class for adapter failures."""


class ValidationError(AdapterError):
    """An adapter-owned request or profile is invalid."""


class ConflictError(AdapterError):
    """An idempotency key was reused with different input."""


class NotFoundError(AdapterError):
    """A task is not present in the configured store."""


class PermissionError(AdapterError):
    """Provider permissions did not match the bound profile."""


class ProtocolError(AdapterError):
    """The provider protocol was malformed or failed."""


class UncertainError(AdapterError):
    """A provider operation may have been delivered but its outcome is unknown."""
