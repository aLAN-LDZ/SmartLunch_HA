# custom_components/smart_lunch/sensor.py
from __future__ import annotations

import logging
from datetime import timedelta, date, datetime
from decimal import Decimal
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DOMAIN

# do dekodowania expiry z ciasteczka
from .api import decode_remember_token_expiry

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    client = data["client"]

    # ---- KOORDYNATOR: FUNDING (zapyta API) ----
    async def _async_update_funding() -> dict[str, Any]:
        try:
            today = date.today().isoformat()
            payload = await client.fetch_funding_for_day(today)
            fs = (payload or {}).get("funding_setting") or {}
            avail = fs.get("available_fundings") or {}
            return {
                "daily_cents": avail.get("daily_cents"),
                "monthly_cents": avail.get("monthly_cents"),
                "raw": payload,
                "source_day": today,
            }
        except Exception as e:
            raise UpdateFailed(str(e)) from e

    funding_coordinator = DataUpdateCoordinator(
        hass,
        logger=_LOGGER,
        name="smart_lunch_funding",
        update_method=_async_update_funding,
        update_interval=timedelta(minutes=30),
    )
    await funding_coordinator.async_config_entry_first_refresh()

    # ---- KOORDYNATOR: TOKEN EXPIRY (bez sieci – tylko odczyt cookie) ----
    async def _async_update_token() -> dict[str, Any]:
        try:
            jar = {c.key: c.value for c in client.session.cookie_jar}
            token = jar.get("remember_user_token")
            exp: datetime | None = decode_remember_token_expiry(token) if token else None
            return {"expiry": exp}
        except Exception as e:
            # nie powinno się zdarzyć, ale gdyby… nie wysadzamy całej platformy
            _LOGGER.debug("Token expiry update failed: %s", e)
            return {"expiry": None}

    token_coordinator = DataUpdateCoordinator(
        hass,
        logger=_LOGGER,
        name="smart_lunch_token_expiry",
        update_method=_async_update_token,
        update_interval=timedelta(minutes=5),
    )
    await token_coordinator.async_config_entry_first_refresh()

    entities = [
        SmartLunchMonthlyFundingRemainingSensor(funding_coordinator, entry),
        SmartLunchTokenExpirySensor(token_coordinator, entry),
    ]
    async_add_entities(entities)


class SmartLunchMonthlyFundingRemainingSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Miesięczne dofinansowanie – pozostało"
    _attr_icon = "mdi:cash"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "PLN"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_monthly_funding_remaining"

    @property
    def available(self) -> bool:
        data = self.coordinator.data or {}
        return data.get("monthly_cents") is not None

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        cents = data.get("monthly_cents")
        if cents is None:
            return None
        pln = (Decimal(int(cents)) / Decimal(100)).quantize(Decimal("0.01"))
        return float(pln)

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        daily_cents = data.get("daily_cents")
        monthly_cents = data.get("monthly_cents")
        attrs = {
            "source_day": data.get("source_day"),
            "daily_cents": daily_cents,
            "monthly_cents": monthly_cents,
        }
        if daily_cents is not None:
            attrs["daily_limit_pln"] = float(
                (Decimal(int(daily_cents)) / Decimal(100)).quantize(Decimal("0.01"))
            )
        if monthly_cents is not None:
            attrs["monthly_remaining_pln"] = float(
                (Decimal(int(monthly_cents)) / Decimal(100)).quantize(Decimal("0.01"))
            )
        return attrs


class SmartLunchTokenExpirySensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Token – data wygaśnięcia"
    _attr_icon = "mdi:timer-sand-complete"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_state_class = None  # timestamp nie powinien mieć state_class

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_token_expiry"

    @property
    def available(self) -> bool:
        data = self.coordinator.data or {}
        return data.get("expiry") is not None

    @property
    def native_value(self):
        """Zwraca timezone-aware datetime (UTC) lub None."""
        data = self.coordinator.data or {}
        exp: datetime | None = data.get("expiry")
        return exp  # HA oczekuje obiektu datetime dla device_class=timestamp

    @property
    def extra_state_attributes(self):
        # nic szczególnego – można dopisać surowe cookie albo źródło, ale to wrażliwe
        return {}