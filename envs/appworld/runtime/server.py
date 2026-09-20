"""Trusted AppWorld service. Accept named public APIs, never student Python code."""

import json
import io
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from appworld import AppWorld

MAX_REQUEST_BYTES = 1_000_000
REQUEST_INPUT_TIMEOUT = 10.0
RESPONSE_TIMEOUT = 10.0


class DeadlineInput(io.RawIOBase):
    """Bound the entire input phase, even when a peer keeps dripping bytes."""
    def __init__(self, connection):
        self.connection = connection
        # AppWorld freezes ordinary time.monotonic; this Linux clock stays real.
        self.deadline = time.clock_gettime(time.CLOCK_MONOTONIC) + REQUEST_INPUT_TIMEOUT

    def readable(self):
        return True

    def remaining(self):
        remaining = self.deadline - time.clock_gettime(time.CLOCK_MONOTONIC)
        if remaining <= 0:
            raise TimeoutError('HTTP request input deadline exceeded')
        return remaining

    def readinto(self, buffer):
        self.connection.settimeout(self.remaining())
        return self.connection.recv_into(buffer)


class Server(HTTPServer):
    def __init__(self, address, task_id):
        self.world = AppWorld(task_id=task_id, experiment_name="rollout", load_ground_truth=False)
        self.api_docs = self.world.task.api_docs
        self.apis = self.world.shell.user_ns["apis"]
        self.call_count = 0
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.rfile.close()
        self.input = DeadlineInput(self.connection)
        self.rfile = io.BufferedReader(self.input)

    def reply(self, status, payload):
        body = json.dumps(payload, default=str).encode()
        self.connection.settimeout(RESPONSE_TIMEOUT)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.input.remaining()
        self.connection.settimeout(RESPONSE_TIMEOUT)
        url = urlsplit(self.path)
        world = self.server.world
        if url.path == "/health":
            return self.reply(200, {"ready": True})
        if url.path == "/task":
            return self.reply(200, {"task_id": world.task_id, "instruction": world.task.instruction,
                                   "datetime": world.task.datetime.isoformat()})
        if url.path == "/status":
            return self.reply(200, {"task_completed": world.task_completed()})
        if url.path == "/docs":
            query = parse_qs(url.query)
            app = query.get("app", [""])[0]
            endpoint = query.get("endpoint", [""])[0]
            docs = self.server.api_docs
            if app not in docs or app == "admin":
                return self.reply(404, {"error": "Unknown public app", "apps": list(docs)})
            if endpoint:
                endpoint = endpoint.removeprefix(app + "__")
                if endpoint not in docs[app]:
                    return self.reply(404, {"error": "Unknown endpoint"})
                return self.reply(200, docs[app][endpoint])
            return self.reply(200, {name: spec["description"].splitlines()[0]
                                   for name, spec in docs[app].items()})
        self.reply(404, {"error": "Unknown route"})

    def do_POST(self):
        if self.path != "/call":
            return self.reply(404, {"error": "Unknown route"})
        try:
            lengths = self.headers.get_all('Content-Length', [])
            if self.headers.get_all('Transfer-Encoding') or len(lengths) != 1:
                return self.reply(400, {'error': 'Exactly one Content-Length and no Transfer-Encoding required'})
            value = lengths[0].strip()
            if not value.isascii() or not value.isdecimal():
                return self.reply(400, {'error': 'Invalid Content-Length'})
            length = int(value)
            if not 0 < length <= MAX_REQUEST_BYTES:
                return self.reply(413, {"error": "Invalid request size"})
            raw = self.rfile.read(length)
            self.input.remaining()
            if len(raw) != length:
                return self.reply(400, {'error': 'Incomplete request body'})
            body = json.loads(raw)
            if not isinstance(body, dict) or set(body) != {"app", "api", "kwargs"}:
                return self.reply(400, {"error": "Expected app, api and kwargs"})
            app, api, kwargs = body["app"], body["api"], body["kwargs"]
            if not isinstance(app, str) or not isinstance(api, str) or not isinstance(kwargs, dict):
                return self.reply(400, {"error": "Invalid call types"})
            if app == "admin" or app not in self.server.api_docs or api not in self.server.api_docs[app]:
                return self.reply(403, {"error": "Only documented public APIs are available"})
        except (ValueError, TypeError):
            return self.reply(400, {"error": "Invalid JSON request"})
        # The input deadline must not interrupt a valid, potentially slow API.
        self.connection.settimeout(RESPONSE_TIMEOUT)
        self.server.call_count += 1
        if self.server.call_count > 80_000:
            return self.reply(429, {"error": "Task API call budget exhausted"})
        try:
            result = self.server.apis[app][api](**kwargs)
            payload = {"result": result}
            status = 200
        except Exception as exc:
            # API validation/auth errors are observations; never expose a server traceback.
            payload = {"error": str(exc)}
            status = 422
        finally:
            # Persist partial mutations even if an API raised after changing state.
            world = self.server.world
            world._save_state(world.output_db_home_path_on_disk)
            world.save_logs()
        self.reply(status, payload)


if __name__ == "__main__":
    task_id = os.environ["APPWORLD_TASK_ID"]
    if not (Path("/opt/appworld/data/tasks") / task_id / "specs.json").is_file():
        raise ValueError("Configured task is unavailable")
    with Server(("0.0.0.0", 8000), task_id) as server:
        print(f"AppWorld public API service ready: {task_id}", flush=True)
        server.serve_forever()
