from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SmartLunchClient
from .const import DOMAIN, PLATFORMS


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass, verify_ssl=True)
    email: str = entry.data["email"]
    base: str = entry.data.get("base")

    client = SmartLunchClient(hass, email, None, base, session=session)

    cookies: dict[str, str] = entry.data.get("cookies", {})
    if cookies:
        client.attach_cookies(cookies)
        if not await client.validate_session():
            # Bez hasła nie próbujemy cichego relogu – pozwalamy HA wywołać reauth
            raise ConfigEntryAuthFailed("Session expired")
    else:
        # Brak cookies – wymagamy logowania przez flow
        raise ConfigEntryAuthFailed("No session; reauth required")

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"client": client}

    if PLATFORMS:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if PLATFORMS:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    else:
        unload_ok = True
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok