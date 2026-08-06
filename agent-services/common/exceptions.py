"""Shared exception hierarchy for all agent-services modules.

Leaf module — must not import from any other project module.

The hierarchy::

    Exception
     └── AppError
         ├── ConfigError      — config loading / validation failures
         ├── NotifyError      — Discord notification failures
         ├── FetchError       — API / network fetch failures
         └── AnalysisError    — analysis / computation failures
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for all application-level errors raised by agent-services."""

    default_message: str = "application error"

    def __init__(self, message: str = "") -> None:
        # If no message is provided, fall back to a per-class default so the
        # rendered __str__ still carries meaningful information.
        if not message:
            message = self.default_message
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return f"{type(self).__name__}: {self.message}"


class ConfigError(AppError):
    """Raised when configuration loading or validation fails."""

    default_message: str = "configuration error"


class NotifyError(AppError):
    """Raised when a Discord notification dispatch fails irrecoverably."""

    default_message: str = "notification delivery failed"


class FetchError(AppError):
    """Raised when an external API or network fetch fails."""

    default_message: str = "fetch failed"


class AnalysisError(AppError):
    """Raised when an analysis / computation step fails."""

    default_message: str = "analysis failed"


__all__ = [
    "AppError",
    "ConfigError",
    "NotifyError",
    "FetchError",
    "AnalysisError",
]
