# custom_components/smart_lunch/__init__.py
from __future__ import annotations

from yarl import URL
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
            raise ConfigEntryAuthFailed("Session expired")
    else:
        raise ConfigEntryAuthFailed("No session; reauth required")

    base_url = URL(client.base)
    device_identifiers = {(DOMAIN, f"{email.lower()}|{base_url.host}")}

    device_info = {
        "identifiers": device_identifiers,            # WYMAGANE do spięcia encji z urządzeniem
        "name": f"SmartLunch ({email})",             # Nazwa urządzenia w HA
        "configuration_url": str(base_url),          # Link „Konfiguracja” w karcie urządzenia
        "manufacturer": "SmartLunch",                # opcjonalnie
        "model": "API",                             # opcjonalnie
    }

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "device_info": device_info,  # 👈 udostępniamy platformom
    }

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