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

# Klucze w entry.options – tu trzymamy lokalne wybory
OPT_SELECTED_PLACE_ID = "selected_delivery_place_id"
OPT_SELECTED_DAY = "selected_delivery_day"
OPT_SELECTED_HOUR = "selected_delivery_hour"


def _safe_update_entry_options(hass: HomeAssistant, entry: ConfigEntry, patch: dict[str, Any]) -> None:
    """Bezpiecznie nadpisz część options (merge)."""
    new_options = dict(entry.options)
    new_options.update(patch)
    hass.config_entries.async_update_entry(entry, options=new_options)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    client = data["client"]
    device_info = data.get("device_info") or {}

    # ------------------------------
    # SELECT 1: MIEJSCE DOSTAWY
    # ------------------------------
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

    # ------------------------------
    # SELECT 2: DATA DOSTAWY (zależny od miejsca)
    # ------------------------------
    async def _async_update_days() -> dict[str, Any]:
        """Pobierz dostępne daty dla aktualnego miejsca (ZAWSZE z serwera)."""
        try:
            # Aktualne miejsce – lokalny wybór albo fallback do serwerowego
            current_place_id = entry.options.get(OPT_SELECTED_PLACE_ID)
            if current_place_id is None:
                current_place_id = place_coordinator.data.get("server_default_id")

            if current_place_id is None:
                # brak miejsca → brak dat
                return {"place_id": None, "dates": []}

            place_id_int = int(current_place_id)

            # Pobierz daty dla miejsca
            dd = await client.fetch_delivery_dates(place_id_int)
            dates = []
            for item in dd.get("delivery_dates", []):
                d = item.get("date")
                if d:
                    dates.append(d)

            # Obecny lokalny wybór dnia – tylko jeśli nadal dostępny
            selected_day = entry.options.get(OPT_SELECTED_DAY)
            if selected_day not in dates:
                selected_day = None

            return {
                "place_id": place_id_int,
                "dates": dates,           # list[str] YYYY-MM-DD
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

    # ------------------------------
    # SELECT 3: GODZINA DOSTAWY (zależny od miejsca i dnia)
    # ------------------------------
    async def _async_update_hours() -> dict[str, Any]:
        """Pobierz dostępne godziny dla aktualnego miejsca i dnia (ZAWSZE z serwera)."""
        try:
            # 1) Miejsce – jak wyżej
            current_place_id = entry.options.get(OPT_SELECTED_PLACE_ID)
            if current_place_id is None:
                current_place_id = place_coordinator.data.get("server_default_id")
            if current_place_id is None:
                return {"place_id": None, "day": None, "hours": []}
            place_id_int = int(current_place_id)

            # 2) Dzień – musi być wybrany
            current_day = entry.options.get(OPT_SELECTED_DAY)
            if not current_day:
                # Jeśli brak wyboru dnia, nie mamy jak wyliczyć godzin
                return {"place_id": place_id_int, "day": None, "hours": []}

            # 3) Pobierz daty (z godzinami) dla miejsca i wyciągnij godziny dla wybranego dnia
            dd = await client.fetch_delivery_dates(place_id_int)
            hours: list[str] = []
            for item in dd.get("delivery_dates", []):
                if item.get("date") == current_day:
                    for h in item.get("hours", []) or []:
                        if isinstance(h, str):
                            hours.append(h)
                    break

            # 4) Obecny lokalny wybór godziny – tylko jeśli nadal dostępna
            selected_hour = entry.options.get(OPT_SELECTED_HOUR)
            if selected_hour not in hours:
                selected_hour = None

            return {
                "place_id": place_id_int,
                "day": current_day,
                "hours": hours,                 # list[str] "HH:MM"
                "selected_hour": selected_hour,
                "raw": dd,
            }
        except Exception as e:
            raise UpdateFailed(str(e)) from e

    hour_coordinator = DataUpdateCoordinator(
        hass,
        logger=_LOGGER,
        name="smart_lunch_delivery_hour_select",
        update_method=_async_update_hours,
        update_interval=timedelta(minutes=15),
    )
    await hour_coordinator.async_config_entry_first_refresh()

    hour_entity = SmartLunchDeliveryHourSelect(hass, hour_coordinator, entry, device_info)
    async_add_entities([hour_entity])

    # ------------------------------
    # Reakcje na zmiany: miejsce → odśwież daty i godziny; dzień → odśwież godziny
    # Z porównaniem poprzednich wartości (naprawia "odbicie" na unknown).
    # ------------------------------
    # cache poprzednich wartości
    last_place_id = entry.options.get(OPT_SELECTED_PLACE_ID) or place_coordinator.data.get("server_default_id")
    last_day = entry.options.get(OPT_SELECTED_DAY)

    @callback
    async def _on_options_changed(hass: HomeAssistant, updated_entry: ConfigEntry) -> None:
        nonlocal last_place_id, last_day
        if updated_entry.entry_id != entry.entry_id:
            return

        new_place_id = updated_entry.options.get(OPT_SELECTED_PLACE_ID) or place_coordinator.data.get("server_default_id")
        new_day = updated_entry.options.get(OPT_SELECTED_DAY)

        # 1) Zmiana miejsca?
        if new_place_id != last_place_id:
            await day_coordinator.async_request_refresh()
            await hour_coordinator.async_request_refresh()

            # Po odświeżeniu, jeśli obecna godzina nie jest dostępna – wyczyść ją
            hc = hour_coordinator.data or {}
            sel_hour = updated_entry.options.get(OPT_SELECTED_HOUR)
            if sel_hour and sel_hour not in (hc.get("hours") or []):
                _safe_update_entry_options(hass, updated_entry, {OPT_SELECTED_HOUR: None})

            # Zaktualizuj cache
            last_place_id = new_place_id

        # 2) Zmiana dnia?
        if new_day != last_day:
            await hour_coordinator.async_request_refresh()
            # Po odświeżeniu, jeśli obecna godzina nie jest dostępna – wyczyść ją
            hc = hour_coordinator.data or {}
            sel_hour = updated_entry.options.get(OPT_SELECTED_HOUR)
            if sel_hour and sel_hour not in (hc.get("hours") or []):
                _safe_update_entry_options(hass, updated_entry, {OPT_SELECTED_HOUR: None})

            # Zaktualizuj cache
            last_day = new_day

    entry.add_update_listener(_on_options_changed)


# ===========================
# Encje
# ===========================

class SmartLunchDeliveryPlaceSelect(CoordinatorEntity, SelectEntity):
    """Select: wybór miejsca dostawy (opcje z serwera, zapis lokalny)."""

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

    @property
    def current_option(self) -> str | None:
        """Zawsze pokazuj ostatni zapisany wybór (jeśli jest)."""
        data = self.coordinator.data or {}
        id_to_name: dict[int, str] = data.get("id_to_name") or {}
        sid = self._entry.options.get(OPT_SELECTED_PLACE_ID)
        try:
            return id_to_name.get(int(sid)) if sid is not None else None
        except Exception:
            return None

    async def async_select_option(self, option: str) -> None:
        data = self.coordinator.data or {}
        id_to_name: dict[int, str] = data.get("id_to_name") or {}
        name_to_id = {v: k for k, v in id_to_name.items()}
        place_id = name_to_id.get(option)

        if place_id is None:
            _LOGGER.warning("Nie znaleziono ID dla opcji '%s'", option)
            return

        _safe_update_entry_options(self.hass, self._entry, {OPT_SELECTED_PLACE_ID: int(place_id)})
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        return {
            "selected_id": self._entry.options.get(OPT_SELECTED_PLACE_ID),
            "server_default_id": data.get("server_default_id"),
            "id_to_name": data.get("id_to_name"),
        }


class SmartLunchDeliveryDaySelect(CoordinatorEntity, SelectEntity):
    """Select: wybór daty dostawy (opcje z serwera, zależne od miejsca, zapis lokalny)."""

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
        """Zawsze pokazuj ostatni zapisany wybór (jeśli jest)."""
        sel = self._entry.options.get(OPT_SELECTED_DAY)
        return sel if sel else None

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


class SmartLunchDeliveryHourSelect(CoordinatorEntity, SelectEntity):
    """Select: wybór godziny dostawy (opcje z serwera, zależne od miejsca i dnia, zapis lokalny)."""

    _attr_has_entity_name = True
    _attr_name = "Godzina dostawy"
    _attr_icon = "mdi:clock-time-four-outline"
    _attr_state_class = None

    def __init__(self, hass: HomeAssistant, coordinator: DataUpdateCoordinator, entry: ConfigEntry, device_info: dict) -> None:
        super().__init__(coordinator)
        self.hass = hass
        self._entry = entry
        self._device_info = device_info
        self._attr_unique_id = f"{entry.entry_id}_delivery_hour_select"
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
        return bool(data.get("hours"))

    @property
    def options(self) -> list[str]:
        data = self.coordinator.data or {}
        return data.get("hours") or []

    @property
    def current_option(self) -> str | None:
        """Zawsze pokazuj ostatni zapisany wybór (jeśli jest)."""
        sel = self._entry.options.get(OPT_SELECTED_HOUR)
        return sel if sel else None

    async def async_select_option(self, option: str) -> None:
        data = self.coordinator.data or {}
        hours = data.get("hours") or []
        if option not in hours:
            _LOGGER.warning(
                "Wybrana godzina '%s' nie jest dostępna dla place_id=%s i day=%s",
                option, data.get("place_id"), data.get("day")
            )
            return
        _safe_update_entry_options(self.hass, self._entry, {OPT_SELECTED_HOUR: option})
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        return {
            "place_id": data.get("place_id"),
            "day": data.get("day"),
            "hours_count": len(data.get("hours") or []),
        }