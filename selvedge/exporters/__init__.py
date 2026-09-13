"""Export/import adapters that bridge Selvedge's native model to other formats.

Currently:

- :mod:`selvedge.exporters.agent_trace` — Agent Trace v0.1.0 producer/consumer
  (an open AI-attribution wire format published by Cursor; frozen at v0.1.0,
  spec at agent-trace.dev).

These live outside the frozen top-level ``selvedge`` API surface on purpose:
they are interop adapters, not core library types. Import them from the
submodule (``from selvedge.exporters.agent_trace import ...``).
"""
