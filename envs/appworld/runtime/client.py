"""Student-side CLI and persistent Python session; contains no world state or grader."""

import argparse
import contextlib
import functools
import json
import multiprocessing
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import ModuleType

WORLD_URL = "http://world:8000"
SESSION_URL = "http://127.0.0.1:8001"
MAX_OUTPUT_CHARS = 12000
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(base, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(req, timeout=300) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read())
        raise RuntimeError(body.get("error", str(exc))) from None


class APIs(ModuleType):
    def __init__(self):
        super().__init__("apis")
        self.__path__ = []
        for name in ("amazon", "api_docs", "file_system", "gmail", "phone", "simple_note",
                     "splitwise", "spotify", "supervisor", "todoist", "venmo"):
            app = App(name)
            setattr(self, name, app)
            sys.modules[f"apis.{name}"] = app

    def __getattr__(self, app):
        if app.startswith("__"):
            raise AttributeError(app)
        return App(app)


class App(ModuleType):
    def __init__(self, name):
        super().__init__(f"apis.{name}")
        self.name = name

    def __getattr__(self, api):
        if api.startswith("__"):
            raise AttributeError(api)
        return functools.partial(self.call, api)

    def call(self, api, **kwargs):
        return request(WORLD_URL, "/call", {"app": self.name, "api": api, "kwargs": kwargs})["result"]


class Requester:
    def request(self, app_name, api_name, **kwargs):
        return App(app_name).call(api_name, **kwargs)


class OutputBuffer:
    """Bound memory use while retaining output emitted before an exception."""
    def __init__(self):
        self.head = ""
        self.tail = ""
        self.count = 0

    def write(self, value):
        self.count += len(value)
        remaining = MAX_OUTPUT_CHARS // 2 - len(self.head)
        self.head += value[:remaining]
        self.tail = (self.tail + value[remaining:])[-MAX_OUTPUT_CHARS // 2:]
        return len(value)

    def flush(self):
        pass

    def text(self):
        if self.count <= MAX_OUTPUT_CHARS:
            return self.head + self.tail
        return self.head + f"\n... [{self.count - MAX_OUTPUT_CHARS} characters omitted] ...\n" + self.tail


class StreamOutput:
    def __init__(self, connection):
        self.connection = connection

    def write(self, value):
        for start in range(0, len(value), 4096):
            self.connection.send(("output", value[start:start + 4096]))
        return len(value)

    def flush(self):
        pass


def run_worker(connection, task_datetime):
    from datetime import datetime
    from appworld.apps.api_lib import set_local_date_and_time

    # The supervisor keeps real monotonic time; only student Python uses the task clock.
    clock = set_local_date_and_time(datetime.fromisoformat(task_datetime))
    namespace = {"__name__": "__main__", "apis": APIs(), "requester": Requester()}
    # Common import spellings resolve to the same proxies, never to world internals.
    sys.modules["apis"] = namespace["apis"]
    requester_module = ModuleType("requester")
    requester_module.request = namespace["requester"].request
    sys.modules["requester"] = requester_module
    output = StreamOutput(connection)
    try:
        while True:
            code = connection.recv()
            error_type = None
            try:
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    exec(compile(code, "<appworld-exec>", "exec"), namespace)
            except BaseException as exc:
                error_type = type(exc).__name__
                output.write(traceback.format_exc())
            connection.send(("done", error_type))
    except EOFError:
        pass
    finally:
        clock.stop()


class ExecutionSession:
    def __init__(self, task_datetime):
        self.task_datetime = task_datetime
        self.worker = None
        self.connection = None

    def close(self):
        if self.worker is not None:
            if self.worker.is_alive():
                self.worker.kill()
            self.worker.join()
            self.connection.close()
            self.worker = None

    def execute(self, code, timeout):
        if self.worker is None or not self.worker.is_alive():
            self.close()
            ctx = multiprocessing.get_context("spawn")
            self.connection, child = ctx.Pipe()
            self.worker = ctx.Process(target=run_worker, args=(child, self.task_datetime), daemon=True)
            self.worker.start()
            child.close()
        output = OutputBuffer()
        deadline = time.monotonic() + timeout
        error_type = None
        reset = False
        try:
            self.connection.send(code)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self.connection.poll(remaining):
                    raise TimeoutError("Python execution exceeded its deadline")
                kind, value = self.connection.recv()
                if kind == "done":
                    error_type = value
                    break
                output.write(value)
        except (TimeoutError, EOFError, BrokenPipeError, ConnectionResetError) as exc:
            error_type = type(exc).__name__
            reset = True
            self.close()
            output.write(f"\n{error_type}: Python session stopped; variables were reset. "
                         "App state persists. An API request already in flight may still finish; "
                         "read back affected entities before retrying.\n")
        return {"output": output.text(), "error": error_type is not None,
                "error_type": error_type, "session_reset": reset}


class Session(HTTPServer):
    def __init__(self):
        task = request(WORLD_URL, "/task")
        self.execution = ExecutionSession(task["datetime"])
        super().__init__(("127.0.0.1", 8001), SessionHandler)

    def server_close(self):
        self.execution.close()
        super().server_close()


class SessionHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1_000_000:
                raise ValueError("Invalid code request size")
            payload = json.loads(self.rfile.read(length))
            code = payload["code"]
            timeout = payload.get("timeout", 100)
            if not isinstance(code, str) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 120:
                raise ValueError("Code must be text and timeout must be between 0 and 120 seconds")
            result = self.server.execution.execute(code, timeout)
        except (ValueError, KeyError, TypeError) as exc:
            result = {"output": str(exc), "error": True,
                      "error_type": type(exc).__name__, "session_reset": False}
        try:
            completed = request(WORLD_URL, "/status")["task_completed"]
        except (RuntimeError, OSError):
            completed = None
        result["task_completed"] = completed
        body = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    run = sub.add_parser("exec")
    run.add_argument("--file", help="Python file; otherwise read stdin")
    run.add_argument("--json", action="store_true")
    run.add_argument("--timeout", type=float, default=100, help="Python deadline in seconds (maximum 120)")
    docs = sub.add_parser("docs")
    docs.add_argument("app")
    docs.add_argument("endpoint", nargs="?", default="")
    sub.add_parser("status")
    sub.add_parser("task")
    args = parser.parse_args()
    if args.command == "serve":
        with Session() as session:
            session.serve_forever()
    elif args.command == "exec":
        if args.file:
            with open(args.file) as file:
                code = file.read()
        else:
            code = sys.stdin.read()
        result = request(SESSION_URL, "/execute", {"code": code, "timeout": args.timeout})
        if args.json:
            print(json.dumps(result))
        else:
            print(result["output"], end="")
            metadata = {key: value for key, value in result.items() if key != "output"}
            print("\n[AppWorld result] " + json.dumps(metadata))
            if result["error"]:
                raise SystemExit(1)
    elif args.command == "docs":
        query = urllib.parse.urlencode({"app": args.app, "endpoint": args.endpoint})
        print(json.dumps(request(WORLD_URL, "/docs?" + query), indent=2))
    else:
        print(json.dumps(request(WORLD_URL, "/" + args.command), indent=2))


if __name__ == "__main__":
    main()
