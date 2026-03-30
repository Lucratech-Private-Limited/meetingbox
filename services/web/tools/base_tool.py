"""Shared baseline for agent tools (concrete tools ship with orchestrator / integrations)."""


class ToolError(RuntimeError):
  """Raised when a tool invocation fails; message is safe to log or surface to the caller."""
