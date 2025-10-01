# custom_components/smart_lunch/select.py
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    client = data["client"]
    device_info = data.get("device_info")

    async def _async_update_places() -> dict[str, Any]:
        try:
            dp = await client.fetch_delivery_places()
            # spłaszcz listę opcji i znajdź domyślną
            options: list[tuple[int, str]] = []
            current_id: int | None = None
            for comp in dp.get("companies_delivery_places", []):
                for loc in comp.get("delivery_places", []):
                    pid = loc.get("id")
                    name = loc.get("name_pl") or loc.get("name") or f"Place {pid}"
                    options.append((pid, name))
                    if loc.get("default") is True:
                        current_id = pid
            # mapowania pomocnicze
            id_to_name = {pid: name for pid, name in options}
            return {
                "options": options,          # [(id, name), ...]
                "current_id": current_id,    # int | None
                "current_name": id_to_name.get(current_id) if current_id is not None else None,
                "id_to_name": id_to_name,
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

    async_add_entities([SmartLunchDeliveryPlaceSelect(coordinator, entry, device_info)])


class SmartLunchDeliveryPlaceSelect(CoordinatorEntity, SelectEntity):
    _attr_has_entity_name = True
    _attr_name = "Miejsce dostawy"
    _attr_icon = "mdi:map-marker"
    _attr_state_class = None

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry, device_info: dict | None) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._device_info = device_info or {}
        self._attr_unique_id = f"{entry.entry_id}_delivery_place_select"

    @property
    def device_info(self) -> dict | None:
        return self._device_info or None

    @property
    def available(self) -> bool:
        data = self.coordinator.data or {}
        return bool(data.get("options"))

    @property
    def options(self) -> list[str]:
        data = self.coordinator.data or {}
        opts = data.get("options") or []
        return [name for _, name in opts]

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data or {}
        return data.get("current_name")

    async def async_select_option(self, option: str) -> None:
        """
        Na razie READ-ONLY: potrzebujemy endpointu do ustawienia domyślnej lokalizacji na serwerze.
        Gdy go podasz, tutaj:
          - znajdziemy place_id po nazwie,
          - wyślemy POST/PUT,
          - await self.coordinator.async_request_refresh()
        """
        _LOGGER.warning(
            "Zmiana miejsca dostawy nieaktywna: brak endpointu zapisu. Wybrano w UI: %s (ignoruję).",
            option,
        )
        # odśwież bieżący stan z serwera (nic nie zmieni w praktyce)
        await self.coordinator.async_request_refresh()