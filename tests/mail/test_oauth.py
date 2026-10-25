"""Signing in to Microsoft: in the browser or with a device code, tokens refreshed silently, a private cache."""

import sys

import pytest

from glean.connectors.mail import oauth, options
from glean.errors import GleanError


def opts(**kwargs):
    fields = {
        "address": "john@example.com",
        "client_id": "cid",
        "authority": options.CONSUMERS,
        "redirect_uri": "http://localhost",
        "host": "h",
        "port": 993,
        "folders": (),
        "exclude_folders": (),
        "my_addresses": (),
        "archive": None,
        "collection": "mail",
        "save_attachments": (),
        "skip_senders": (),
        "eml_folder": None,
    }
    return options.MailOptions(**(fields | kwargs))


class App:
    def __init__(self, result=None, flow=None, silent=None, accounts=()):
        self.result, self.flow, self.silent, self.accounts, self.calls = result, flow, silent, list(accounts), []

    def get_accounts(self, username=None):
        return [a for a in self.accounts if username in (None, a["username"])]

    def acquire_token_interactive(self, scopes, **kwargs):
        self.calls.append(("interactive", scopes, kwargs))
        return self.result

    def initiate_device_flow(self, scopes):
        return self.flow

    def acquire_token_by_device_flow(self, flow):
        self.calls.append(("device", flow))
        return self.result

    def acquire_token_silent(self, scopes, account):
        self.calls.append(("silent", account))
        return self.silent

    def remove_account(self, account):
        self.accounts.remove(account)


SIGNED_IN = {"access_token": "AT", "id_token_claims": {"preferred_username": "john@example.com"}}


def test_login_in_the_browser(tmp_path):
    app, said = App(SIGNED_IN), []
    account = oauth.Account(opts(redirect_uri="http://localhost:8400"), tmp_path / "t.bin", app)
    assert account.login(say=said.append) == "john@example.com"
    _, scopes, kwargs = app.calls[0]
    assert scopes == oauth.SCOPES
    assert kwargs["login_hint"] == "john@example.com"
    assert kwargs["port"] == 8400
    assert "Opening your browser" in said[0]


def test_login_with_a_device_code(tmp_path):
    app, said = App(SIGNED_IN, flow={"user_code": "ABC", "message": "Go to https://microsoft.com/devicelogin"}), []
    assert oauth.Account(opts(), tmp_path / "t.bin", app).login(device_code=True, say=said.append)
    assert said == ["Go to https://microsoft.com/devicelogin"]


def test_a_device_flow_that_does_not_start(tmp_path):
    app = App(flow={"error": "invalid_client", "error_description": "AADSTS700016: app not found"})
    with pytest.raises(GleanError, match="AADSTS700016"):
        oauth.Account(opts(), tmp_path / "t.bin", app).login(device_code=True, say=print)


@pytest.mark.parametrize(
    "result, message",
    [({"error": "access_denied"}, "access_denied"), ({}, "no reason given")],
)
def test_a_failed_sign_in(tmp_path, result, message):
    with pytest.raises(GleanError, match=message):
        oauth.Account(opts(), tmp_path / "t.bin", App(result)).login(say=print)


def test_signing_in_as_someone_else_warns(tmp_path):
    said = []
    result = {"access_token": "AT", "id_token_claims": {"preferred_username": "other@example.com"}}
    oauth.Account(opts(), tmp_path / "t.bin", App(result)).login(say=said.append)
    assert "warning: signed in as other@example.com" in said[-1]


def test_tokens_are_refreshed_silently(tmp_path):
    me = {"username": "john@example.com"}
    app = App(silent={"access_token": "fresh"}, accounts=[{"username": "x@y"}, me])
    account = oauth.Account(opts(), tmp_path / "t.bin", app)
    assert account.token() == "fresh"
    assert app.calls == [("silent", me)]
    assert account.signed_in() == "john@example.com"


def test_no_token_without_a_sign_in(tmp_path):
    account = oauth.Account(opts(), tmp_path / "t.bin", App())
    assert account.signed_in() is None
    with pytest.raises(GleanError, match="isn't signed in"):
        account.token()


def test_an_expired_sign_in(tmp_path):
    account = oauth.Account(opts(), tmp_path / "t.bin", App(silent=None, accounts=[{"username": "john@example.com"}]))
    with pytest.raises(GleanError, match="has expired"):
        account.token()


def test_logout_forgets_every_account(tmp_path):
    app = App(accounts=[{"username": "a"}, {"username": "b"}])
    assert oauth.Account(opts(), tmp_path / "t.bin", app).logout() == 2
    assert app.accounts == []


def test_a_client_id_is_needed(tmp_path):
    with pytest.raises(GleanError, match="client_id is missing"):
        oauth.Account(opts(client_id=None), tmp_path / "t.bin")


def test_a_real_application_with_its_cache(tmp_path):
    account = oauth.Account(opts(), tmp_path / "home" / "t.bin")
    assert account.signed_in() is None  # an empty cache; no network needed


def test_the_cache_is_private(tmp_path):
    path = tmp_path / "t.bin"
    cache = oauth.token_cache(path)
    cache.add({"client_id": "cid", "scope": ["s"], "token_endpoint": "https://login.example/token",
               "response": {"access_token": "SECRET-TOKEN", "token_type": "Bearer", "expires_in": 3600}})  # fmt: skip
    data = path.read_bytes()
    if sys.platform == "win32":
        assert b"SECRET-TOKEN" not in data  # encrypted with DPAPI
    else:
        assert path.stat().st_mode & 0o077 == 0  # only its owner may read it
    again = oauth.token_cache(path)  # an existing file, opened again
    assert list(again.search(again.CredentialType.ACCESS_TOKEN))


def test_each_systems_cache(tmp_path, monkeypatch):
    plain = oauth.token_cache(tmp_path / "plain.bin", platform="linux")
    plain.add({"client_id": "cid", "scope": ["s"], "token_endpoint": "https://login.example/token",
               "response": {"access_token": "T", "token_type": "Bearer", "expires_in": 3600}})  # fmt: skip
    assert b'"secret": "T"' in (tmp_path / "plain.bin").read_bytes()
    built = []
    monkeypatch.setattr(oauth, "build_encrypted_persistence", lambda p: built.append(p) or oauth.FilePersistence(p))
    oauth.token_cache(tmp_path / "enc.bin", platform="win32")
    assert built == [str(tmp_path / "enc.bin")]


def test_an_authority_microsoft_does_not_know_is_named(tmp_path, monkeypatch):
    def refuse(client_id, authority, token_cache):
        raise ValueError("Unable to get authority configuration for https://login.example/nope")

    monkeypatch.setattr(oauth.msal, "PublicClientApplication", refuse)
    with pytest.raises(GleanError, match=r"mail\.authority .*https://login\.example/nope.*Unable to get authority"):
        oauth.Account(opts(authority="https://login.example/nope"), tmp_path / "t.bin")
