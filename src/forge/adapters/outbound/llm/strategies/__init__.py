"""LLM tool-calling 및 structured-output 전략."""

class ToolCallingError(ValueError):
    """A provider returned an invalid native tool-call envelope."""

__all__ = [
    "ToolCallingError",
]
