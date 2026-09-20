"""Exercise the real HTTP handler without task data or a model deployment."""
import http.client
import importlib.util
import json
from pathlib import Path
import socket
import sys
import threading
import time
from types import SimpleNamespace

import pytest


@pytest.fixture
def service(monkeypatch):
    calls = []
    def echo(**kwargs):
        calls.append(kwargs)
        return {'ok': True}
    world = SimpleNamespace(
        task=SimpleNamespace(api_docs={'phone': {'echo': {}}}),
        shell=SimpleNamespace(user_ns={'apis': {'phone': {'echo': echo}}}),
        output_db_home_path_on_disk='unused',
        _save_state=lambda path: None, save_logs=lambda: None)
    monkeypatch.setitem(sys.modules, 'appworld', SimpleNamespace(AppWorld=lambda **kwargs: world))
    spec = importlib.util.spec_from_file_location('http_input_test_server',
        Path(__file__).resolve().parents[1]/'runtime/server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'REQUEST_INPUT_TIMEOUT', 0.2)
    monkeypatch.setattr(module.Handler, 'log_message', lambda *args: None)
    server = module.Server(('127.0.0.1', 0), 'fake-task')
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    thread.start()
    yield SimpleNamespace(module=module, server=server, calls=calls, world=world)
    server.shutdown()
    server.server_close()
    thread.join(timeout=1)
    assert not thread.is_alive()


@pytest.mark.parametrize('prefix', [
    b'POST /call HTTP/1.0\r\nX-Incomplete: ',
    b'POST /call HTTP/1.0\r\nContent-Length: 1000\r\n\r\n',
])
def test_dripping_header_and_body_have_absolute_real_deadline(service,monkeypatch,prefix):
    # Simulate the frozen AppWorld time function; deadline must still advance.
    monkeypatch.setattr(service.module.time, 'monotonic', lambda: 1234.0)
    connection = socket.create_connection(service.server.server_address, timeout=1)
    connection.sendall(prefix)
    started = time.clock_gettime(time.CLOCK_MONOTONIC)
    stopped = threading.Event()
    def drip():
        while not stopped.wait(0.02):
            try: connection.sendall(b'x')
            except OSError: return
    sender = threading.Thread(target=drip, daemon=True)
    sender.start()
    try:
        try: assert connection.recv(1024) == b''
        except ConnectionResetError: pass
        elapsed = time.clock_gettime(time.CLOCK_MONOTONIC) - started
        assert 0.1 <= elapsed < 0.9
    finally:
        stopped.set()
        connection.close()
        sender.join(timeout=1)
    assert not service.calls
    client = http.client.HTTPConnection(*service.server.server_address, timeout=1)
    client.request('GET', '/health')
    assert client.getresponse().status == 200
    client.close()


@pytest.mark.parametrize('headers,body', [
    ('Content-Length: 2\r\nContent-Length: 2\r\n', b'{}'),
    ('Content-Length: 2\r\nTransfer-Encoding: chunked\r\n', b'{}'),
    ('Content-Length: 1000001\r\n', b''),
    ('Content-Length: -1\r\n', b''),
    ('Content-Length: 10\r\n', b'{}'),
])
def test_malformed_request_never_calls_world(service,headers,body):
    with socket.create_connection(service.server.server_address, timeout=1) as connection:
        connection.sendall(b'POST /call HTTP/1.0\r\n'+headers.encode()+b'\r\n'+body)
        connection.shutdown(socket.SHUT_WR)
        reply = connection.recv(4096)
    assert reply.startswith((b'HTTP/1.0 400',b'HTTP/1.0 413'))
    assert not service.calls


def test_complete_buffered_body_and_slow_api_outlive_input_deadline(service):
    def slow(**kwargs):
        service.calls.append(kwargs)
        threading.Event().wait(0.3)
        return {'ok': True}
    service.world.shell.user_ns['apis']['phone']['echo'] = slow
    payload = json.dumps({'app':'phone','api':'echo','kwargs':{'text':'x'*900_000}})
    client = http.client.HTTPConnection(*service.server.server_address, timeout=2)
    client.request('POST','/call',body=payload)
    response = client.getresponse()
    assert response.status == 200 and json.loads(response.read()) == {'result':{'ok':True}}
    client.close()
    assert len(service.calls)==1 and len(service.calls[0]['text'])==900_000
