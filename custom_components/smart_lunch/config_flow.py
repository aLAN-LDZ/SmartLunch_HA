from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import ConfigEntryAuthFailed
import voluptuous as vol

from .api import SmartLunchClient
from .const import DOMAIN, DEFAULT_BASE

DATA_SCHEMA = vol.Schema(
    {
        vol.Required("email"): str,
        vol.Required("password"): str,
        vol.Optional("base", default=DEFAULT_BASE): str,
    }
)


async def _do_login(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    client = SmartLunchClient(hass, data["email"], data["password"], data["base"])
    tokens = await client.login()  # ValueError jeśli błąd
    ok = await client.validate_session()
    if not ok:
        raise ValueError("Login ok, ale /users nie zwróciło 200")
    return tokens


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA)

        errors: dict[str, str] = {}
        try:
            tokens = await _do_login(self.hass, user_input)
        except ValueError:
            errors["base"] = "invalid_auth"
        except Exception:
            errors["base"] = "cannot_connect"

        if errors:
            return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA, errors=errors)

        await self.async_set_unique_id(user_input["email"].lower())
        self._abort_if_unique_id_configured()

        # NIE zapisujemy hasła w entry – tylko email, base, cookies
        entry_data = {
            "email": user_input["email"],
            "base": user_input["base"],
            "cookies": tokens.get("cookies", {}),
            "remember_exp": tokens.get("remember_exp"),
        }
        return self.async_create_entry(
            title=f"SmartLunch ({user_input['email']})",
            data=entry_data,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Wejście reauth – znamy email, prosimy tylko o hasło i odświeżamy cookies."""
        # entry_id jest w context; pobierz istniejący entry
        entry = self.hass.config_entries.async_get_entry(self.context.get("entry_id"))
        self._reauth_entry = entry
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        entry = getattr(self, "_reauth_entry", None)
        assert entry is not None
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema({vol.Required("password"): str}),
                description_placeholders={"email": entry.data.get("email", "")},
            )
        # wykonaj login transientnie
        client = SmartLunchClient(
            self.hass, entry.data["email"], user_input["password"], entry.data.get("base", DEFAULT_BASE)
        )
        tokens = await client.login()
        new_data = dict(entry.data)
        new_data["cookies"] = tokens.get("cookies", {})
        new_data["remember_exp"] = tokens.get("remember_exp")
        # nie zapisujemy password
        self.hass.config_entries.async_update_entry(entry, data=new_data)
        await self.hass.config_entries.async_reload(entry.entry_id)
        return self.async_abort(reason="reauth_successful")