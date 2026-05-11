"""Standard HAL exceptions used by adapters and hub."""

from __future__ import annotations


class HalError(RuntimeError):
    def __init__(self, message: str, *, code: str = "hal_error", cause: Exception | None = None):
        super().__init__(message)
        self.code = code
        self.cause = cause


class HalConnectionError(HalError):
    def __init__(self, message: str, *, cause: Exception | None = None):
        super().__init__(message, code="connection_error", cause=cause)


class HalNotConnectedError(HalError):
    def __init__(self, message: str = "Device is not connected"):
        super().__init__(message, code="not_connected")


class HalBusyError(HalError):
    def __init__(self, message: str = "Device is busy"):
        super().__init__(message, code="busy")


class HalTimeoutError(HalError):
    def __init__(self, message: str = "Operation timed out", *, cause: Exception | None = None):
        super().__init__(message, code="timeout", cause=cause)


class HalCommandError(HalError):
    def __init__(self, message: str, *, cause: Exception | None = None):
        super().__init__(message, code="command_error", cause=cause)
