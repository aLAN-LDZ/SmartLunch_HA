from __future__ import annotations

import base64
import json
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from aiohttp import ClientSession, ClientTimeout
from yarl import URL
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import (
    DEFAULT_BASE,
    LOGIN_PATH,
    USERS_ME_PATH,
    USER_AGENT,
    COOKIE_KEYS,
    HTTP_TIMEOUT,
)

META_CSRF_RE = re.compile(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', re.I)


def _b64_fix_padding(s: str) -> bytes:
    s2 = urllib.parse.unquote_plus(s.strip())
    s2 += "=" * ((4 - len(s2) % 4) % 4)
    return base64.urlsafe_b64decode(s2.encode("utf-8"))


def decode_remember_token_expiry(token_value: str) -> Optional[datetime]:
    try:
        raw_unquoted = urllib.parse.unquote_plus(token_value)
        first = raw_unquoted.split("--", 1)[0]
        outer = json.loads(_b64_fix_padding(first).decode("utf-8", errors="replace"))
        if isinstance(outer, dict) and "_rails" in outer:
            exp_str = outer["_rails"].get("exp")
            if exp_str:
                return datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
    except Exception:
        return None
    return None


@dataclass
class AuthState:
    csrf: Optional[str] = None
    token_exp: Optional[datetime] = None


class SmartLunchClient:
    """Minimalny klient do logowania i walidacji sesji (async)."""

    def __init__(
        self,
        hass: HomeAssistant,
        email: str,
        password: str | None,
        base: str = DEFAULT_BASE,
        session: ClientSession | None = None,
    ) -> None:
        self.hass = hass
        self.email = email
        self._password = password  # tylko transientnie (nie zapisujemy w entry)
        # utwardź base: akceptuj wartości bez schematu
        _base = base.rstrip("/")
        if "://" not in _base:
            _base = f"https://{_base}"
        self.base = _base
        self.base_url = URL(self.base)

        self.session: ClientSession = session or async_get_clientsession(
            hass, verify_ssl=True
        )
        self.auth = AuthState()
        self._headers = {
            "User-Agent": f"{USER_AGENT} (HA {HA_VERSION})",
            "Accept": "application/json",
        }

    async def _preflight_csrf(self) -> None:
        try:
            async with self.session.get(
                f"{self.base}/",
                timeout=ClientTimeout(total=HTTP_TIMEOUT),
                headers={"User-Agent": self._headers["User-Agent"]},
            ) as r:
                text = await r.text()
                m = META_CSRF_RE.search(text)
                if m:
                    self.auth.csrf = m.group(1)
        except Exception:
            self.auth.csrf = None

    def _json_headers(self) -> dict[str, str]:
        h = dict(self._headers)
        h["Content-Type"] = "application/json"
        if self.auth.csrf:
            h["X-CSRF-Token"] = self.auth.csrf
        h["Origin"] = self.base
        h["Referer"] = f"{self.base}/"
        h["X-Requested-With"] = "XMLHttpRequest"
        return h

    async def login(self) -> dict[str, Any]:
        if not self._password:
            raise ConfigEntryAuthFailed("Password required for login")
        await self._preflight_csrf()
        payload = {"user": {"login": self.email, "password": self._password}}
        async with self.session.post(
            f"{self.base}{LOGIN_PATH}",
            headers=self._json_headers(),
            data=json.dumps(payload).encode("utf-8"),
            timeout=ClientTimeout(total=HTTP_TIMEOUT),
        ) as r:
            resp_json: dict[str, Any] | None = None
            ctype = r.headers.get("Content-Type", "")
            if ctype.startswith("application/json"):
                try:
                    resp_json = await r.json()
                except Exception:
                    resp_json = None

            jar = {c.key: c.value for c in self.session.cookie_jar}
            ok = (
                r.status == 200
                and (resp_json or {}).get("success") is True
                and "remember_user_token" in jar
            )
            if not ok:
                detail = resp_json or (await r.text())[:300]
                raise ValueError(f"Login failed: {r.status} {detail}")

            token_exp = decode_remember_token_expiry(jar.get("remember_user_token", ""))
            self.auth.token_exp = token_exp
            return {
                "cookies": {k: v for k, v in jar.items() if k in COOKIE_KEYS},
                "remember_exp": token_exp.isoformat() if token_exp else None,
            }

    async def validate_session(self) -> bool:
        try:
            async with self.session.get(
                f"{self.base}{USERS_ME_PATH}",
                headers=self._headers,
                timeout=ClientTimeout(total=HTTP_TIMEOUT),
            ) as r:
                return r.status == 200 and (
                    r.headers.get("Content-Type", "").startswith("application/json")
                )
        except Exception:
            return False

    def attach_cookies(self, cookies: dict[str, str]) -> None:
        """Wstaw znane ciastka do cookie_jar (wymaga obiektu URL)."""
        self.session.cookie_jar.clear()
        # możesz podać wszystkie na raz:
        self.session.cookie_jar.update_cookies(cookies, response_url=self.base_url)
        # (albo iteracyjnie – ale powyższe wystarcza)

    async def _request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        """
        Pomocniczy wrapper do przyszłych wywołań API:
        - 401/403/419 → ConfigEntryAuthFailed (HA uruchomi reauth)
        - inne błędy → raise_for_status
        """
        url = f"{self.base}{path}"
        async with self.session.request(
            method,
            url,
            headers=self._headers,
            timeout=ClientTimeout(total=HTTP_TIMEOUT),
            **kwargs,
        ) as r:
            if r.status in (401, 403, 419):
                raise ConfigEntryAuthFailed("Session expired")
            r.raise_for_status()
            # zakładamy JSON – dostosujemy w razie innych endpointów
            return await r.json()