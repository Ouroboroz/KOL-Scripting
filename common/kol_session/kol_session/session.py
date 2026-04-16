"""
KoL session management — HMAC-MD5 challenge-response login.

Usage (context manager, recommended):
    with KoLSession.from_env() as session:
        resp = session.get("api.php", params={"what": "status", "for": "my-bot"})
        print(resp.json())

Usage (manual):
    session = KoLSession(username="...", password="...")
    session.login()
    session.get("charpane.php")
    session.logout()

Credentials are read (in order of priority):
    1. Passed explicitly to KoLSession(username=, password=)
    2. Environment variables KOL_USERNAME / KOL_PASSWORD
    3. A .env file in the current working directory (or any parent up to the
       filesystem root), loaded automatically via python-dotenv.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://www.kingdomofloathing.com"

# Regex patterns for scraping the login page / post-login redirect
_PLAYER_ID_RE = re.compile(r'var playerid = (\d+);')
_PLAYER_NAME_RE = re.compile(r'href="charsheet.php"><b>(\w+)</b>')
_PWDHASH_RE = re.compile(r'var\s+pwdhash\s*=\s*["\']([^"\']+)["\']')


def _load_dotenv() -> None:
    """Best-effort .env load — silent if python-dotenv is absent."""
    try:
        from dotenv import load_dotenv  # type: ignore[import-untyped]
    except ImportError:
        return
    # Walk up from cwd looking for a .env file
    cwd = Path.cwd()
    for directory in [cwd, *cwd.parents]:
        env_file = directory / ".env"
        if env_file.is_file():
            load_dotenv(env_file)
            log.debug("Loaded .env from %s", env_file)
            return


class KoLSession:
    """Authenticated Kingdom of Loathing HTTP session.

    The underlying ``httpx.Client`` maintains the cookie jar across requests,
    so both ``PHPSESSID`` and ``appserver`` cookies are sent automatically on
    every call — mirroring what KolMafia's GenericRequest does in Java.
    """

    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        *,
        timeout: float = 30.0,
    ) -> None:
        _load_dotenv()

        self._username: str = username or os.environ.get("KOL_USERNAME", "")
        self._password: str = password or os.environ.get("KOL_PASSWORD", "")

        if not self._username or not self._password:
            raise ValueError(
                "KoL credentials required.\n"
                "  • Pass username/password to KoLSession(), or\n"
                "  • Set KOL_USERNAME and KOL_PASSWORD environment variables, or\n"
                "  • Create a .env file with those variables."
            )

        self._client = httpx.Client(
            base_url=BASE_URL,
            follow_redirects=True,
            timeout=timeout,
        )

        self.player_id: str | None = None
        self.player_name: str | None = None
        self.pwdhash: str | None = None   # per-session token required for all write requests

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_env(
        cls,
        *,
        timeout: float = 30.0,
    ) -> "KoLSession":
        """Create a session reading credentials from environment / .env file."""
        return cls(timeout=timeout)

    # ── Authentication ────────────────────────────────────────────────────────

    def _get_player_info(self) -> None:
        """Calls charpane.php to grab player relate informationsuch as player name and player id.

        This should only be called after a successful login where we parse the pane content
        for the regex for the player info
        """

        log.info("Fetching player name and id from charpane")

        # Fetch charpane.php to extract the per-session pwdhash token.
        # This token is required as a POST field ("pwd") on every mutating request
        # (crafting, buying, using items, etc.) — KoL uses it as a CSRF guard.
        try:
            charpane = self._client.get("/charpane.php")
            if m := _PWDHASH_RE.search(charpane.text):
                self.pwdhash = m.group(1)
                log.debug("pwdhash extracted (%s…)", self.pwdhash[:8])
            else:
                log.warning("Could not extract pwdhash from charpane.php — write requests will fail")
            
            if player_id_match := _PLAYER_ID_RE.search(charpane.text):
                self.player_id = player_id_match.group(1)
                log.debug(f"Extracted player id {self.player_id}")
            else:
                log.warning("Could not successfully extract player id from charpane.php")

            if player_name_match := _PLAYER_NAME_RE.search(charpane.text):
                self.player_name = player_name_match.group(1)
                log.debug(f"Extracted player name {self.player_name}")
            else:
                log.warning("Could not successfully extract player name from charpane.php")

        except Exception as exc:  # noqa: BLE001
            log.warning("charpane.php fetch failed: %s — write requests may fail", exc)

    def login(self) -> None:
        """Perform the full HMAC-MD5 challenge-response login.

        After a successful login the httpx cookie jar holds ``PHPSESSID`` and
        ``appserver``; both are sent automatically on every subsequent request.

        Raises:
            RuntimeError: if no ``PHPSESSID`` cookie is returned (bad credentials
                or KoL server error).
            httpx.HTTPStatusError: on non-2xx HTTP responses.
        """
        log.info("Fetching login challenge for %s", self._username)

        log.info("Posting login for %s", self._username)
        resp = self._client.post(
            "/login.php",
            data={
                "loggingin": "Yup.",
                "loginname": self._username,
                "password": self._password,          # intentionally blank — KoL's JS clears it
                "secure": "0",
            },
        )
        resp.raise_for_status()

        session_id = self._client.cookies.get("PHPSESSID")
        if not session_id:
            raise RuntimeError(
                "Login failed — no PHPSESSID cookie returned. "
                "Double-check KOL_USERNAME / KOL_PASSWORD."
            )

        log.info(
            "Logged in as %s (player_id=%s, PHPSESSID=%s…)",
            self._username,
            self.player_id,
            session_id[:8],
        )

        self._get_player_info()

    def logout(self) -> None:
        """POST /logout.php and clear the session cookie."""
        try:
            self._client.get("/logout.php")
            log.info("Logged out %s", self._username)
        except Exception as exc:  # noqa: BLE001
            log.warning("Logout request failed (ignored): %s", exc)
        finally:
            self._client.cookies.clear()
            self.player_id = None
            self.player_name = None
            self.pwdhash = None

    def close(self) -> None:
        """Close the underlying HTTP client (frees connection pool)."""
        self._client.close()

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> "KoLSession":
        self.login()
        return self

    def __exit__(self, *_: Any) -> None:
        self.logout()
        self.close()

    # ── State ─────────────────────────────────────────────────────────────────

    @property
    def is_logged_in(self) -> bool:
        """True if the session holds a PHPSESSID cookie."""
        return bool(self._client.cookies.get("PHPSESSID"))

    def __repr__(self) -> str:
        status = f"player_id={self.player_id}" if self.is_logged_in else "not logged in"
        return f"KoLSession(username={self._username!r}, {status})"

    # ── Authenticated requests ────────────────────────────────────────────────

    def _check_auth(self) -> None:
        if not self.is_logged_in:
            raise RuntimeError(
                "Not logged in. Call session.login() or use KoLSession as a context manager."
            )

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        """Authenticated GET to a KoL endpoint.

        Args:
            path: Relative path, e.g. ``"api.php"`` or ``"/charpane.php"``.
            **kwargs: Passed through to ``httpx.Client.get()`` (e.g. ``params=``).
        """
        self._check_auth()
        url = path if path.startswith("/") else f"/{path}"
        return self._client.get(url, **kwargs)

    def post(self, path: str, data: dict[str, Any], **kwargs: Any) -> httpx.Response:
        """Authenticated POST to a KoL endpoint.

        Args:
            path: Relative path, e.g. ``"inv_use.php"``.
            data: Form fields as a dict.
            **kwargs: Passed through to ``httpx.Client.post()``.
        """
        self._check_auth()
        url = path if path.startswith("/") else f"/{path}"
        return self._client.post(url, data=data, **kwargs)
