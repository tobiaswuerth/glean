"""Signing in to Microsoft with OAuth2, in glean's own process: the browser opens Microsoft's sign-in page, and MSAL
receives the answer on a local port (or, with a device code, on another device). The tokens are kept in a cache in
GLEAN_HOME, encrypted with the user's login on Windows (DPAPI), and refreshed without asking while they can be."""

import os
import sys
from urllib.parse import urlparse

import msal
from msal_extensions import FilePersistence, PersistedTokenCache, build_encrypted_persistence

from ... import console
from ...errors import GleanError

SCOPES = ["https://outlook.office.com/IMAP.AccessAsUser.All"]  # MSAL adds offline_access, openid and profile
LOGIN_TIMEOUT = 300  # seconds to finish signing in in the browser
APP_GUIDE = "https://github.com/tobiaswuerth/glean/blob/main/docs/microsoft-app.md"


def token_cache(path, platform=sys.platform):
    """The token cache at `path`: encrypted where the system offers it (DPAPI on Windows), else a file only its
    owner may read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if platform == "win32":
        persistence = build_encrypted_persistence(str(path))
    else:  # macOS' keychain and Linux' secret service would ask or fail without a desktop session
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))  # owner only; later writes keep the mode
        os.chmod(path, 0o600)
        persistence = FilePersistence(str(path))
    return PersistedTokenCache(persistence)


class Account:
    """The mail account's sign-in: `login()` once, then `token()` whenever IMAP needs one."""

    def __init__(self, options, cache_path, app=None):
        self.options = options
        if app is None:
            if not options.client_id:
                raise GleanError(f"mail.client_id is missing in glean.toml: see {APP_GUIDE}")
            try:
                app = msal.PublicClientApplication(
                    options.client_id, authority=options.authority, token_cache=token_cache(cache_path)
                )
            except ValueError as e:  # MSAL asks the authority for its configuration right away
                raise GleanError(f"mail.authority {options.authority} can't be used: {e}") from None
        self.app, self.cache_path = app, cache_path

    def _accounts(self):
        return self.app.get_accounts(username=self.options.address) or self.app.get_accounts()

    def login(self, device_code=False, say=console.say):
        """Sign in interactively; returns the account signed in."""
        if device_code:
            flow = self.app.initiate_device_flow(scopes=SCOPES)
            if "user_code" not in flow:
                raise GleanError(f"Microsoft refused to start the sign-in: {_error(flow)}")
            say(flow["message"])
            result = self.app.acquire_token_by_device_flow(flow)
        else:
            say(f"Opening your browser to sign in as {self.options.address}; if it doesn't open, use --device-code.")
            result = self.app.acquire_token_interactive(
                SCOPES,
                login_hint=self.options.address,
                prompt="select_account",
                port=urlparse(self.options.redirect_uri).port,
                timeout=LOGIN_TIMEOUT,
            )
        if "access_token" not in result:
            raise GleanError(f"the sign-in failed: {_error(result)}")

        user = (result.get("id_token_claims") or {}).get("preferred_username") or self.options.address
        if user.lower() != self.options.address:
            say(f"warning: signed in as {user}, but glean.toml names {self.options.address}")
        return user

    def signed_in(self):
        """The account the cache holds tokens for; None if none."""
        accounts = self._accounts()
        return accounts[0]["username"] if accounts else None

    def token(self):
        """A current access token, refreshed if it has expired."""
        accounts = self._accounts()
        if not accounts:
            raise GleanError(f"{self.options.address} isn't signed in: run `glean mail login`")
        result = self.app.acquire_token_silent(SCOPES, account=accounts[0])
        if not result or "access_token" not in result:
            raise GleanError(f"the sign-in of {self.options.address} has expired: run `glean mail login`")
        return result["access_token"]

    def logout(self):
        """Forget every account's tokens; returns how many accounts there were."""
        accounts = self.app.get_accounts()
        for account in accounts:
            self.app.remove_account(account)
        return len(accounts)


def _error(result):
    return result.get("error_description") or result.get("error") or "no reason given"
