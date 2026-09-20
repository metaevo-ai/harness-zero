"""Bridge: deepagents `SandboxBackendProtocol` -> harbor `BaseEnvironment`.

Lets a deepagents graph run host-side (inside the harbor process, where the
project .venv already has deepagents installed) while every shell command and
file operation executes in the remote sandbox through harbor's environment
handle (`exec` / `upload_file` / `download_file`).

Hidden plumbing for `deepagents_harbor/` — outer harnesses written
as top-level packages should import `DeepHarnessAgent` from `base.py`
and never need to touch this file.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from deepagents.backends.protocol import (
    FILE_NOT_FOUND,
    IS_DIRECTORY,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

if TYPE_CHECKING:
    from harbor.environments.base import BaseEnvironment


class HarborSandboxBackend(BaseSandbox):
    """`BaseSandbox` delegating `execute` / file transfer to a harbor environment.

    All higher-level file tools (ls/read/write/edit/grep/glob) are derived by
    `BaseSandbox` on top of the async primitives overridden here, so the whole
    deepagents tool surface works unchanged against the sandbox.
    """

    def __init__(
        self,
        environment: BaseEnvironment,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
        default_timeout_sec: int = 600,
    ) -> None:
        self._env = environment
        # Captured from the agent's async `run()`; used by the sync fallbacks,
        # which only ever fire from `asyncio.to_thread` worker threads.
        self._loop = loop
        self._default_timeout_sec = default_timeout_sec

    @property
    def id(self) -> str:
        return f"harbor-{self._env.session_id}"

    @property
    def environment_name(self) -> str:
        return self._env.environment_name

    @property
    def session_id(self) -> str:
        return self._env.session_id

    # ------------------------------------------------------------------ async

    async def aexecute(
        self, command: str, *, timeout: int | None = None
    ) -> ExecuteResponse:
        result = await self._env.exec(
            command, timeout_sec=timeout or self._default_timeout_sec
        )
        return ExecuteResponse(
            # ExecResult fields are None when empty (docker env); f-stringing
            # None would append the literal text "None" and corrupt outputs
            # that must stay machine-parseable (deepagents' JSON read/edit
            # protocols).
            output=(result.stdout or "") + (result.stderr or ""),
            exit_code=result.return_code,
            truncated=False,
        )

    async def aupload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        for remote_path, content in files:
            # harbor's upload_file reads from a local path, so stage the bytes.
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(content)
                tmp_path = Path(tmp.name)
            try:
                await self._env.upload_file(tmp_path, remote_path)
                responses.append(FileUploadResponse(path=remote_path, error=None))
            except Exception:
                responses.append(
                    FileUploadResponse(path=remote_path, error=FILE_NOT_FOUND)
                )
            finally:
                tmp_path.unlink(missing_ok=True)
        return responses

    async def adownload_files(
        self, paths: list[str]
    ) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for remote_path in paths:
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp_path = Path(tmp.name)
            try:
                await self._env.download_file(remote_path, tmp_path)
                responses.append(
                    FileDownloadResponse(
                        path=remote_path, content=tmp_path.read_bytes(), error=None
                    )
                )
            except Exception as exc:
                error = IS_DIRECTORY if "dir" in str(exc).lower() else FILE_NOT_FOUND
                responses.append(
                    FileDownloadResponse(path=remote_path, content=None, error=error)
                )
            finally:
                tmp_path.unlink(missing_ok=True)
        return responses

    # ------------------------------------------------------------------ sync
    # Fallbacks for callers that bypass the async API; they hop back onto the
    # agent's event loop from a worker thread.

    def _run_sync(self, coro):  # noqa: ANN001, ANN202
        if self._loop is None:
            msg = "HarborSandboxBackend sync API used without a captured event loop"
            raise RuntimeError(msg)
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def execute(
        self, command: str, *, timeout: int | None = None
    ) -> ExecuteResponse:
        return self._run_sync(self.aexecute(command, timeout=timeout))

    def upload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list[FileUploadResponse]:
        return self._run_sync(self.aupload_files(files))

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self._run_sync(self.adownload_files(paths))
