"""Switch platform for Folding@home."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FAHDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switches."""
    coordinator: FAHDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FAHFoldingSwitch(coordinator, entry)])


class FAHFoldingSwitch(CoordinatorEntity[FAHDataUpdateCoordinator], SwitchEntity):
    """Switch to control folding."""

    _attr_icon = "mdi:play-pause"
    _attr_has_entity_name = True
    _attr_translation_key = "folding"

    def __init__(
        self,
        coordinator: FAHDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize switch."""
        super().__init__(coordinator)

        # Use machine_id from entry data (set during config flow) for stable identification
        # Fall back to entry.unique_id (also the machine_id) or entry_id as last resort
        self._machine_id = entry.data.get("machine_id") or entry.unique_id or entry.entry_id
        self._machine_name = entry.data.get("machine_name", "FAH Client")

        self._attr_unique_id = f"{self._machine_id}_folding"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        info = (self.coordinator.data.get("info") or {}) if self.coordinator.data else {}
        return DeviceInfo(
            identifiers={(DOMAIN, self._machine_id)},
            name=self._machine_name,
            manufacturer="Folding@home",
            model=f"FAH Client {info.get('version', 'Unknown')}",
            sw_version=info.get("version"),
        )

    @property
    def is_on(self) -> bool:
        """Return true if folding."""
        if not self.coordinator.data:
            return False
        # State is in the default group's config, not top-level config
        groups = self.coordinator.data.get("groups") or {}
        default_group = groups.get("") or {}
        config = default_group.get("config") or {}
        return not config.get("paused", True)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Resume folding."""
        await self.coordinator.async_send_command({"cmd": "state", "state": "fold", "group": ""})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Pause folding."""
        await self.coordinator.async_send_command({"cmd": "state", "state": "pause", "group": ""})
