"""Where orbit's vault is: the resource orbit://vault, read in an MCP session of glean's own, of a local fake server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from glean import orbit

REAL_VAULT = orbit.vault  # before the tests' fixture keeps every test from the real orbit
VAULT = {"vault": "D:\\crm vault", "version": "0.3.0", "notes": {"person": 3}}


def rpc(request_id, result):
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result})


class Orbit(BaseHTTPRequestHandler):
    """orbit's MCP endpoint, as far as glean asks it: initialize, the initialized notification, resources/read of
    orbit://vault, and closing the session. `server.answers` changes what it says."""

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        method, answers = request["method"], self.server.answers
        self.server.requests.append(
            (method, self.headers.get("mcp-session-id"), self.headers.get("mcp-protocol-version"))
        )
        if method == "notifications/initialized":
            self.send_response(202)
            self.end_headers()
            return
        if method == "initialize":
            body = rpc(request["id"], {"protocolVersion": "2025-06-18", "capabilities": {"resources": {}}})
        else:
            assert request["params"] == {"uri": "orbit://vault"}
            body = answers.get("read") or rpc(
                request["id"], {"contents": [{"uri": "orbit://vault", "text": json.dumps(VAULT)}]}
            )
        self.send_response(answers.get("status", 200))
        self.send_header("Content-Type", answers.get("kind", "text/event-stream"))
        if answers.get("session", "s-1"):
            self.send_header("mcp-session-id", answers.get("session", "s-1"))
        self.end_headers()
        sse = answers.get("kind", "text/event-stream") == "text/event-stream"
        self.wfile.write((f"event: message\ndata: {body}\n\n" if sse else body).encode())

    def do_DELETE(self):
        self.server.requests.append(("DELETE", self.headers.get("mcp-session-id"), None))
        self.send_response(405 if self.server.answers.get("refuse_delete") else 200)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    started = []

    def start(**answers):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Orbit)
        httpd.requests, httpd.answers = [], answers
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}/mcp/write"

    yield start
    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


def test_the_vault_is_read_from_its_resource(server):
    httpd, url = server()
    assert str(REAL_VAULT(url)) == "D:\\crm vault"
    assert httpd.requests == [
        ("initialize", None, None),
        ("notifications/initialized", "s-1", "2025-06-18"),
        ("resources/read", "s-1", "2025-06-18"),
        ("DELETE", "s-1", None),  # the session closed
    ]


def test_a_server_that_answers_in_plain_json_and_keeps_no_session(server):
    _, url = server(kind="application/json", session=None)
    assert str(REAL_VAULT(url)) == "D:\\crm vault"


def test_a_session_that_cannot_be_closed_is_left(server):
    _, url = server(refuse_delete=True)
    assert REAL_VAULT(url) is not None


@pytest.mark.parametrize(
    "read",
    [
        json.dumps({"jsonrpc": "2.0", "id": 2, "error": {"code": -32002, "message": "Unknown resource"}}),  # orbit 0.2
        rpc(2, {"contents": []}),
        rpc(2, {"contents": [{"text": "not json"}]}),
        rpc(2, {"contents": [{"text": "{}"}]}),
        "not json at all",
    ],
)
def test_an_answer_without_a_vault(server, read):
    _, url = server(read=read)
    assert REAL_VAULT(url) is None


def test_no_server_at_all():
    assert REAL_VAULT("http://127.0.0.1:9/mcp", timeout=1) is None


def test_an_endpoint_that_refuses(server):
    _, url = server(status=406)
    assert REAL_VAULT(url) is None


def test_closing_a_session_on_a_server_that_is_gone():
    orbit._close("http://127.0.0.1:9/mcp", "s-1", timeout=1)  # no error: the server forgets it anyway
