from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0

# Klucz w entry.options – tu zapisujemy lokalny wybór
OPT_SELECTED_PLACE_ID = "selected_delivery_place_id"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    client = data["client"]
    device_info = data.get("device_info") or {}

    async def _async_update_places() -> dict[str, Any]:
        """Pobierz listę miejsc dostawy (ZAWSZE z serwera)."""
        try:
            dp = await client.fetch_delivery_places()
            options: list[tuple[int, str]] = []
            server_default_id: int | None = None

            for comp in dp.get("companies_delivery_places", []):
                for loc in comp.get("delivery_places", []):
                    pid = loc.get("id")
                    name = loc.get("name_pl") or loc.get("name") or f"Place {pid}"
                    if pid is None:
                        continue
                    options.append((int(pid), name))
                    if loc.get("default") is True:
                        server_default_id = int(pid)

            id_to_name = {pid: name for pid, name in options}
            return {
                "options": options,  # [(id, name), ...]
                "id_to_name": id_to_name,
                "server_default_id": server_default_id,
                "raw": dp,
            }
        except Exception as e:
            raise UpdateFailed(str(e)) from e

    coordinator = DataUpdateCoordinator(
        hass,
        logger=_LOGGER,
        name="smart_lunch_delivery_place_select",
        update_method=_async_update_places,
        update_interval=timedelta(minutes=15),
    )
    await coordinator.async_config_entry_first_refresh()

    entity = SmartLunchDeliveryPlaceSelect(hass, coordinator, entry, device_info)
    async_add_entities([entity])

    # Jeśli nie mamy jeszcze lokalnego wyboru – zainicjalizuj z serwera
    if OPT_SELECTED_PLACE_ID not in entry.options:
        server_default_id = coordinator.data.get("server_default_id")
        if server_default_id is not None:
            _safe_update_entry_options(hass, entry, {OPT_SELECTED_PLACE_ID: server_default_id})


def _safe_update_entry_options(hass: HomeAssistant, entry: ConfigEntry, patch: dict[str, Any]) -> None:
    """Bezpiecznie nadpisz część options (merge)."""
    new_options = dict(entry.options)
    new_options.update(patch)
    hass.config_entries.async_update_entry(entry, options=new_options)


class SmartLunchDeliveryPlaceSelect(CoordinatorEntity, SelectEntity):
    """Select: lokalny wybór miejsca dostawy (opcje z serwera)."""

    _attr_has_entity_name = True
    _attr_name = "Miejsce dostawy (lokalny wybór)"
    _attr_icon = "mdi:map-marker"
    _attr_state_class = None

    def __init__(self, hass: HomeAssistant, coordinator: DataUpdateCoordinator, entry: ConfigEntry, device_info: dict) -> None:
        super().__init__(coordinator)
        self.hass = hass
        self._entry = entry
        self._device_info = device_info
        self._attr_unique_id = f"{entry.entry_id}_delivery_place_select"

        # słuchacz zmian entry.options (np. gdy zmienisz przez OptionsFlow w przyszłości)
        self._unsub_options_listener = entry.add_update_listener(self._async_entry_updated)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_options_listener:
            self._unsub_options_listener()
            self._unsub_options_listener = None

    @callback
    async def _async_entry_updated(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Wywołane gdy entry zostało zaktualizowane (np. options)."""
        if entry.entry_id != self._entry.entry_id:
            return
        self._entry = entry
        self.async_write_ha_state()

    @property
    def device_info(self) -> dict:
        return self._device_info

    @property
    def available(self) -> bool:
        data = self.coordinator.data or {}
        return bool(data.get("options"))

    @property
    def options(self) -> list[str]:
        data = self.coordinator.data or {}
        opts = data.get("options") or []
        return [name for _, name in opts]

    def _selected_id(self) -> int | None:
        """Aktualny lokalny wybór (ID), z fallbackiem na domyślny z serwera."""
        data = self.coordinator.data or {}
        local_id = self._entry.options.get(OPT_SELECTED_PLACE_ID)
        if local_id is not None:
            try:
                return int(local_id)
            except Exception:
                pass
        return data.get("server_default_id")

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data or {}
        id_to_name: dict[int, str] = data.get("id_to_name") or {}
        sid = self._selected_id()
        return id_to_name.get(sid) if sid is not None else None

    async def async_select_option(self, option: str) -> None:
        """Zapisz lokalny wybór do entry.options (nie zmienia serwera)."""
        data = self.coordinator.data or {}
        id_to_name: dict[int, str] = data.get("id_to_name") or {}
        # odwrotne mapowanie
        name_to_id = {v: k for k, v in id_to_name.items()}
        place_id = name_to_id.get(option)

        if place_id is None:
            _LOGGER.warning("Nie znaleziono ID dla opcji '%s'", option)
            return

        # zapisz lokalnie i odśwież widok
        _safe_update_entry_options(self.hass, self._entry, {OPT_SELECTED_PLACE_ID: int(place_id)})
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        sid = self._selected_id()
        return {
            "selected_id": sid,
            "server_default_id": data.get("server_default_id"),
            "id_to_name": data.get("id_to_name"),
        }