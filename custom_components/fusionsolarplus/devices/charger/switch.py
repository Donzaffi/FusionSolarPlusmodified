"""Switch platform for Charger devices."""

import logging
from typing import Dict, Any

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from homeassistant.components.switch import SwitchEntity, ENTITY_ID_FORMAT
from homeassistant.helpers.entity import generate_entity_id

from ...device_handler import BaseDeviceHandler
from ...const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Signal id for Working Status on the charging pile child
SIGNAL_ID_WORKING_STATUS = 10004

# Working Status values that mean charging is active
CHARGING_ACTIVE_STATES = {
    "3",   # Charging
    "8",   # Starting Charging
    "10",  # PV Power Waiting
    "11",  # PV Power Charging
}


# ── Handler ───────────────────────────────────────────────────────────────────

class ChargerSwitchHandler(BaseDeviceHandler):
    """Handler that reads charger data and creates Switch entities."""

    async def _async_get_data(self) -> Dict[str, Any]:
        async def fetch(client):
            return await self.hass.async_add_executor_job(
                client.get_charger_data, self.device_id
            )
        return await self._get_client_and_retry(fetch)

    def create_entities(self, coordinator: DataUpdateCoordinator) -> list:
        return [
            FusionSolarChargerControlSwitch(
                coordinator=coordinator,
                device_info=self.device_info,
            )
        ]


# ── Entity ────────────────────────────────────────────────────────────────────

class FusionSolarChargerControlSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to start/stop EV charging."""

    def __init__(self, coordinator, device_info):
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._optimistic_state = None 

        device_id = list(device_info["identifiers"])[0][1]
        self._attr_unique_id = f"{device_id}_charge_control"
        self._attr_name      = "Charge Control"
        self._attr_icon      = "mdi:ev-station"

        self.entity_id = generate_entity_id(
            ENTITY_ID_FORMAT,
            f"fsp_{device_id}_charge_control",
            hass=coordinator.hass,
        )

    @property
    def is_on(self) -> bool | None:
        if self._optimistic_state is not None:
            return self._optimistic_state

        data = self.coordinator.data
        if not data:
            return None

        value_map = data.get("value_map", {})
        for key, val in value_map.items():
            if isinstance(key, tuple) and key[1] == SIGNAL_ID_WORKING_STATUS:
                try:
                    api_value = str(int(float(val)))
                except (TypeError, ValueError):
                    api_value = str(val)
                is_charging = api_value in CHARGING_ACTIVE_STATES
                if self._optimistic_state is not None and is_charging == self._optimistic_state:
                    self._optimistic_state = None
                return is_charging
        return None

    async def async_turn_on(self, **kwargs) -> None:
        await self._charge_control("start")

    async def async_turn_off(self, **kwargs) -> None:
        await self._charge_control("stop")

    async def _charge_control(self, action: str) -> None:
        device_dn = list(self._attr_device_info["identifiers"])[0][1]
        _LOGGER.debug("Charge control %s → %s", device_dn, action)
        self._optimistic_state = (action == "start")
        self.async_write_ha_state()

        # FIX: Nutzung des Handlers für das Re-Login/Retry Handling
        handler = ChargerSwitchHandler(self.hass, self.coordinator.config_entry, self._attr_device_info)
        
        async def send_command(client):
            return await self.hass.async_add_executor_job(
                client.charge_control, device_dn, action
            )

        try:
            await handler._get_client_and_retry(send_command)
        except Exception as err:
            _LOGGER.error("Charge control %s failed: %s", action, err)
            self._optimistic_state = None
            self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self.coordinator.data is not None


# ── Platform setup ────────────────────────────────────────────────────────────

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    device_name = entry.data.get("device_name")
    device_info = hass.data[DOMAIN].get(f"{entry.entry_id}_device_info")

    if not device_info:
        return

    try:
        handler = ChargerSwitchHandler(hass, entry, device_info)
        coordinator = hass.data[DOMAIN].get(f"{entry.entry_id}_coordinator")
        if coordinator is None:
            return
        entities = handler.create_entities(coordinator)
        async_add_entities(entities)
    except Exception as e:
        _LOGGER.error("Failed to set up switch entities: %s", e)
