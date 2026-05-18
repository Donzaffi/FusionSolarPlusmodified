"""Button platform for Plant devices."""

import asyncio
import logging
from datetime import timedelta
from typing import Dict, Any

from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.components.button import ButtonEntity, ENTITY_ID_FORMAT
from homeassistant.helpers.entity import generate_entity_id

from ...device_handler import BaseDeviceHandler
from ...const import DOMAIN

_LOGGER = logging.getLogger(__name__)

LIVEDATA_DURATION_S  = 60   # FusionSolar remainTime
LIVEDATA_INTERVAL_S  = 6   
NORMAL_INTERVAL_S    = 15   # default coordinator interval


class PlantButtonHandler(BaseDeviceHandler):
    """Handler that creates Button entities for Plant devices."""

    async def _async_get_data(self) -> Dict[str, Any]:
        """Plant button handler reuses the main coordinator — no separate poll."""
        return {}

    def create_entities(self, coordinator) -> list:
        return [
            FusionSolarRefreshButton(
                coordinator=coordinator,
                device_info=self.device_info,
            )
        ]


class FusionSolarRefreshButton(CoordinatorEntity, ButtonEntity):
    """Button that activates FusionSolar live data mode for 60 seconds.

    When pressed:
      1. Subscribes to livedata -> FusionSolar backend updates every 2s
      2. Temporarily sets coordinator update_interval to 2s
      3. After 60s, restores the normal 15s interval
    """

    def __init__(self, coordinator, device_info):
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._live_task = None

        device_id = list(device_info["identifiers"])[0][1]
        self._attr_unique_id = f"{device_id}_live_data"
        self._attr_name = "Live Data"
        self._attr_icon = "mdi:refresh"

        self.entity_id = generate_entity_id(
            ENTITY_ID_FORMAT,
            f"fsp_{device_id}_live_data",
            hass=coordinator.hass,
        )

    async def async_press(self) -> None:
        """Activate live data mode: poll every 2s for 60s then restore 15s."""
        # Cancel any previous live session still running
        if self._live_task and not self._live_task.done():
            self._live_task.cancel()

        device_dn = list(self._attr_device_info["identifiers"])[0][1]
        client = self.hass.data[DOMAIN][self.coordinator.config_entry.entry_id]

        # Subscribe to livedata — backend will serve fresh data every 2s for 60s
        try:
            result = await self.hass.async_add_executor_job(
                client.refresh_livedata, device_dn
            )
            info = result.get("subscribeInfo", {})
            refresh_period = info.get("refreshPeriod", LIVEDATA_INTERVAL_S)
            remain_time    = info.get("remainTime",  LIVEDATA_DURATION_S)
            _LOGGER.debug(
                "Livedata subscribed — refreshPeriod=%ss remainTime=%ss",
                refresh_period, remain_time,
            )
        except Exception as err:
            _LOGGER.warning("Livedata subscribe failed: %s", err)
            refresh_period = LIVEDATA_INTERVAL_S
            remain_time    = LIVEDATA_DURATION_S

        # Switch coordinator to fast mode and schedule restore
        self._live_task = asyncio.ensure_future(
            self._live_session(refresh_period, remain_time)
        )

    async def _live_session(self, refresh_period: int, remain_time: int) -> None:
        """Run fast poll for remain_time seconds then restore normal interval."""
        _LOGGER.debug("Live session: fast poll every %ss for %ss", refresh_period, remain_time)

        # Switch to fast interval
        self.coordinator.update_interval = timedelta(seconds=refresh_period)
        await self.coordinator.async_request_refresh()

        try:
            await asyncio.sleep(remain_time)
        except asyncio.CancelledError:
            _LOGGER.debug("Live session cancelled")
        finally:
            # Always restore normal interval
            self.coordinator.update_interval = timedelta(seconds=NORMAL_INTERVAL_S)
            await self.coordinator.async_request_refresh()
            _LOGGER.debug("Live session ended — restored %ss interval", NORMAL_INTERVAL_S)

    @property
    def available(self) -> bool:
        return True
