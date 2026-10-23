"""Where orbit's vault is: orbit serves it as the resource orbit://vault, which glean reads as an MCP client would, in
a session of its own. Nothing in orbit is changed."""

import json
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__

RESOURCE = "orbit://vault"
PROTOCOL = "2025-06-18"  # the MCP version glean asks for; the server's answer names the one it speaks
TIMEOUT = 5  # seconds


def vault(url, timeout=TIMEOUT):
    """The folder of the vault the orbit server at `url` serves, or None if it can't be asked or doesn't say (orbit
    before 0.3 has no such resource)."""
    session = None
    try:
        hello = {
            "protocolVersion": PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "glean", "version": __version__},
        }
        session, answer = _post(url, None, None, {"id": 1, "method": "initialize", "params": hello}, timeout)
        version = answer["result"]["protocolVersion"]
        _post(url, session, version, {"method": "notifications/initialized"}, timeout)
        _, read = _post(
            url, session, version, {"id": 2, "method": "resources/read", "params": {"uri": RESOURCE}}, timeout
        )
        return Path(json.loads(read["result"]["contents"][0]["text"])["vault"])
    except urllib.error.HTTPError as e:  # not an MCP endpoint, or not orbit's
        e.close()
        return None
    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, TypeError):
        return None
    finally:
        if session:
            _close(url, session, timeout)


def _post(url, session, version, message, timeout):
    """Send one JSON-RPC message; the session id the server gave, and its answer (None for a notification)."""
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    headers |= {"mcp-session-id": session} if session else {}
    headers |= {"mcp-protocol-version": version} if version else {}
    body = json.dumps({"jsonrpc": "2.0", **message}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers), timeout=timeout) as response:
        given, text = response.headers.get("mcp-session-id") or session, response.read().decode("utf-8", "replace")
    return given, (_answer(text) if "id" in message else None)


def _answer(text):
    """A JSON-RPC answer, sent as JSON or as server-sent events."""
    lines = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")] or [text]
    for line in lines:
        try:
            found = json.loads(line)
        except ValueError:
            continue
        if isinstance(found, dict) and "result" in found:
            return found
    raise ValueError("no answer")


def _close(url, session, timeout):
    request = urllib.request.Request(url, method="DELETE", headers={"mcp-session-id": session})
    try:
        urllib.request.urlopen(request, timeout=timeout).close()
    except urllib.error.HTTPError as e:  # a server that keeps sessions open forgets idle ones by itself
        e.close()
    except (urllib.error.URLError, OSError):
        pass
