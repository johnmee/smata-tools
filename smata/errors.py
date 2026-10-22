"""Every error the package raises.

Kept in its own module so that no other module needs to import a sibling that does
real work: smata.client and smata.config both raise these, and neither should import
the other.
"""
from __future__ import annotations


class SmataError(Exception):
    """Base class for every error this package raises."""


class AuthError(SmataError):
    """Login failed, or the session was rejected."""


class ApiError(SmataError):
    """An API or download request failed."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class ConfigError(SmataError):
    """Bad or missing configuration."""
