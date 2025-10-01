# custom_components/smart_lunch/select.py
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

# Klucze w entry.options – zapis lokalnych wyborów
OPT_SELECTED_PLACE_ID = "selected_delivery_place_id"
OPT_SELECTED_DAY = "selected_delivery_day"


def _safe_update_entry_options(hass: HomeAssistant, entry: ConfigEntry, patch: dict[str, Any]) -> None:
    """Bezpiecznie nadpisz część options (merge)."""
    new_options = dict(entry.options)
    new_options.update(patch)
    hass.config_entries.async_update_entry(entry, options=new_options)


# ===========================
# SELECT 1: MIEJSCE DOSTAWY
# ===========================
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
                    if pid is None:
                        continue
                    name = loc.get("name_pl") or loc.get("name") or f"Place {pid}"
                    options.append((int(pid), name))
                    if loc.get("default") is True:
                        server_default_id = int(pid)

            id_to_name = {pid: name for pid, name in options}
            return {
                "options": options,               # [(id, name), ...]
                "id_to_name": id_to_name,         # {id: name}
                "server_default_id": server_default_id,
                "raw": dp,
            }
        except Exception as e:
            raise UpdateFailed(str(e)) from e

    place_coordinator = DataUpdateCoordinator(
        hass,
        logger=_LOGGER,
        name="smart_lunch_delivery_place_select",
        update_method=_async_update_places,
        update_interval=timedelta(minutes=15),
    )
    await place_coordinator.async_config_entry_first_refresh()

    place_entity = SmartLunchDeliveryPlaceSelect(hass, place_coordinator, entry, device_info)
    async_add_entities([place_entity])

    # Inicjalizacja lokalnego wyboru miejscem domyślnym z serwera, jeśli brak
    if OPT_SELECTED_PLACE_ID not in entry.options:
        server_default_id = place_coordinator.data.get("server_default_id")
        if server_default_id is not None:
            _safe_update_entry_options(hass, entry, {OPT_SELECTED_PLACE_ID: server_default_id})

    # ===========================
    # SELECT 2: DATA DOSTAWY
    # ===========================
    async def _async_update_days() -> dict[str, Any]:
        """Pobierz dostępne daty dla aktualnie wybranego miejsca (ZAWSZE z serwera)."""
        try:
            # 1) Aktualne miejsce – lokalny wybór albo fallback do serwerowego domyślnego
            current_place_id = entry.options.get(OPT_SELECTED_PLACE_ID)
            if current_place_id is None:
                # fallback: weź z ostatnich danych koordynatora miejsc
                current_place_id = place_coordinator.data.get("server_default_id")

            if current_place_id is None:
                # spróbuj jeszcze zaciągnąć miejsca, gdyby coś się wyścigało
                pd = await client.fetch_delivery_places()
                current_place_id = client.choose_default_delivery_place_id(pd)

            if current_place_id is None:
                # brak miejsca – brak dat
                return {"place_id": None, "dates": []}

            try:
                place_id_int = int(current_place_id)
            except Exception:
                place_id_int = current_place_id  # i tak rzuci niżej, ale próbujemy

            # 2) Pobierz daty z serwera dla miejsca
            dd = await client.fetch_delivery_dates(place_id_int)
            # spodziewany format: {"delivery_dates": [{"date":"YYYY-MM-DD","hours":[...]}]}
            dates = []
            for item in dd.get("delivery_dates", []):
                d = item.get("date")
                if not d:
                    continue
                # możesz filtrować po hours != [] jeśli chcesz tylko faktycznie dostępne sloty
                dates.append(d)

            # 3) Obecny lokalny wybór (jeśli wybrana data nie jest już dostępna – current_option=None)
            selected_day = entry.options.get(OPT_SELECTED_DAY)
            if selected_day not in dates:
                selected_day = None

            return {
                "place_id": place_id_int,
                "dates": dates,           # list[str] w ISO YYYY-MM-DD
                "selected_day": selected_day,
                "raw": dd,
            }
        except Exception as e:
            raise UpdateFailed(str(e)) from e

    day_coordinator = DataUpdateCoordinator(
        hass,
        logger=_LOGGER,
        name="smart_lunch_delivery_day_select",
        update_method=_async_update_days,
        update_interval=timedelta(minutes=15),
    )
    await day_coordinator.async_config_entry_first_refresh()

    day_entity = SmartLunchDeliveryDaySelect(hass, day_coordinator, entry, device_info)
    async_add_entities([day_entity])

    # Gdy zmieni się wybór miejsca – odśwież listę dat (i wyczyść lokalny wybór daty jeśli już nie pasuje)
    @callback
    async def _refresh_days_on_place_change(hass: HomeAssistant, updated_entry: ConfigEntry) -> None:
        if updated_entry.entry_id != entry.entry_id:
            return
        if OPT_SELECTED_PLACE_ID in updated_entry.options:
            await day_coordinator.async_request_refresh()
            # Jeśli nowa lista dat nie zawiera poprzednio wybranej – skasuj lokalny wybór
            data = day_coordinator.data or {}
            selected_day = updated_entry.options.get(OPT_SELECTED_DAY)
            dates = data.get("dates") or []
            if selected_day and selected_day not in dates:
                _safe_update_entry_options(hass, updated_entry, {OPT_SELECTED_DAY: None})

    # subskrypcja zmian options (dla obu encji)
    entry.add_update_listener(_refresh_days_on_place_change)


class SmartLunchDeliveryPlaceSelect(CoordinatorEntity, SelectEntity):
    """Select: lokalny wybór miejsca dostawy (opcje z serwera)."""

    _attr_has_entity_name = True
    _attr_name = "Miejsce dostawy"
    _attr_icon = "mdi:map-marker"
    _attr_state_class = None

    def __init__(self, hass: HomeAssistant, coordinator: DataUpdateCoordinator, entry: ConfigEntry, device_info: dict) -> None:
        super().__init__(coordinator)
        self.hass = hass
        self._entry = entry
        self._device_info = device_info
        self._attr_unique_id = f"{entry.entry_id}_delivery_place_select"
        self._unsub_options_listener = entry.add_update_listener(self._async_entry_updated)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_options_listener:
            self._unsub_options_listener()
            self._unsub_options_listener = None

    @callback
    async def _async_entry_updated(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
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
        data = self.coordinator.data or {}
        id_to_name: dict[int, str] = data.get("id_to_name") or {}
        name_to_id = {v: k for k, v in id_to_name.items()}
        place_id = name_to_id.get(option)

        if place_id is None:
            _LOGGER.warning("Nie znaleziono ID dla opcji '%s'", option)
            return

        # zapisz lokalnie i odśwież widok
        _safe_update_entry_options(self.hass, self._entry, {OPT_SELECTED_PLACE_ID: int(place_id)})
        self.async_write_ha_state()
        # daty odświeżą się przez listener w async_setup_entry

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        sid = self._selected_id()
        return {
            "selected_id": sid,
            "server_default_id": data.get("server_default_id"),
            "id_to_name": data.get("id_to_name"),
        }


class SmartLunchDeliveryDaySelect(CoordinatorEntity, SelectEntity):
    """Select: lokalny wybór daty dostawy (opcje z serwera, zależne od miejsca)."""

    _attr_has_entity_name = True
    _attr_name = "Data dostawy"
    _attr_icon = "mdi:calendar"
    _attr_state_class = None

    def __init__(self, hass: HomeAssistant, coordinator: DataUpdateCoordinator, entry: ConfigEntry, device_info: dict) -> None:
        super().__init__(coordinator)
        self.hass = hass
        self._entry = entry
        self._device_info = device_info
        self._attr_unique_id = f"{entry.entry_id}_delivery_day_select"
        self._unsub_options_listener = entry.add_update_listener(self._async_entry_updated)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_options_listener:
            self._unsub_options_listener()
            self._unsub_options_listener = None

    @callback
    async def _async_entry_updated(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        if entry.entry_id != self._entry.entry_id:
            return
        self._entry = entry
        # gdy zmieniły się options, to bieżący wybór mógł się zmienić
        self.async_write_ha_state()

    @property
    def device_info(self) -> dict:
        return self._device_info

    @property
    def available(self) -> bool:
        data = self.coordinator.data or {}
        return bool(data.get("dates"))

    @property
    def options(self) -> list[str]:
        data = self.coordinator.data or {}
        return data.get("dates") or []

    @property
    def current_option(self) -> str | None:
        # preferuj lokalny wybór jeśli nadal dostępny
        sel = self._entry.options.get(OPT_SELECTED_DAY)
        data = self.coordinator.data or {}
        dates = data.get("dates") or []
        if sel in dates:
            return sel
        # albo „brak wybranej”
        return None

    async def async_select_option(self, option: str) -> None:
        data = self.coordinator.data or {}
        dates = data.get("dates") or []
        if option not in dates:
            _LOGGER.warning("Wybrana data '%s' nie jest dostępna dla place_id=%s", option, data.get("place_id"))
            return
        _safe_update_entry_options(self.hass, self._entry, {OPT_SELECTED_DAY: option})
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        return {
            "place_id": data.get("place_id"),
            "dates_count": len(data.get("dates") or []),
        }