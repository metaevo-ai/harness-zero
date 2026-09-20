"""Per-trial backend injection for sandbox-bound tools.

`DeepHarnessAgent.run()` publishes the trial's `HarborSandboxBackend` into
the `current_backend` context var before invoking the graph. Tool functions
(in any custom harness package) read it at call time:

    from deepagents_harbor.current import current_backend

    @tool
    def my_tool(path: str) -> str:
        r = current_backend.get().execute(f"ls {path}")
        ...

Context vars are isolated per asyncio task tree, so concurrent trials never
share a backend — this replaces per-tool `make_tool(backend)` factories.
"""

from __future__ import annotations

import contextvars

current_backend: contextvars.ContextVar = contextvars.ContextVar(
    "current_backend"
)
