# custom_components/smart_lunch/sensor.py
from __future__ import annotations

from datetime import timedelta, date
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

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    client = data["client"]

    async def _async_update_data() -> dict[str, Any]:
        try:
            today = date.today().isoformat()
            payload = await client.fetch_funding_for_day(today)
            # spodziewana struktura:
            # {"funding_setting": {"available_fundings": {"daily_cents": int, "monthly_cents": int}, ...}}
            fs = payload.get("funding_setting", {}) or {}
            avail = fs.get("available_fundings", {}) or {}
            return {
                "daily_cents": avail.get("daily_cents"),
                "monthly_cents": avail.get("monthly_cents"),
                "raw": payload,
                "source_day": today,
            }
        except Exception as e:
            raise UpdateFailed(str(e)) from e

    coordinator = DataUpdateCoordinator(
        hass,
        logger=hass.logger,
        name="smart_lunch_funding",
        update_method=_async_update_data,
        update_interval=timedelta(minutes=30),  # funding raczej nie zmienia się co minutę
    )

    await coordinator.async_config_entry_first_refresh()

    entities = [
        SmartLunchMonthlyFundingRemainingSensor(coordinator, entry),
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
        # unikamy problemów z float – Decimal → 2 miejsca po przecinku
        pln = (Decimal(int(cents)) / Decimal(100)).quantize(Decimal("0.01"))
        return float(pln)

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        daily_cents = data.get("daily_cents")
        monthly_cents = data.get("monthly_cents")
        return {
            "daily_limit_pln": None if daily_cents is None else float(Decimal(int(daily_cents)) / Decimal(100)),
            "monthly_remaining_pln": None if monthly_cents is None else float(Decimal(int(monthly_cents)) / Decimal(100)),
            "daily_cents": daily_cents,
            "monthly_cents": monthly_cents,
            "source_day": data.get("source_day"),
        }