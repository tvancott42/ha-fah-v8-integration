"""Button platform for Folding@home."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
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
    """Set up buttons."""
    coordinator: FAHDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FAHFinishButton(coordinator, entry)])


class FAHFinishButton(CoordinatorEntity[FAHDataUpdateCoordinator], ButtonEntity):
    """Button to finish current WUs then pause."""

    _attr_icon = "mdi:stop"
    _attr_has_entity_name = True
    _attr_translation_key = "finish"

    def __init__(
        self,
        coordinator: FAHDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize button."""
        super().__init__(coordinator)

        # Use machine_id from entry data (set during config flow) for stable identification
        # Fall back to entry.unique_id (also the machine_id) or entry_id as last resort
        self._machine_id = entry.data.get("machine_id") or entry.unique_id or entry.entry_id
        self._machine_name = entry.data.get("machine_name", "FAH Client")

        self._attr_unique_id = f"{self._machine_id}_finish"

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

    async def async_press(self) -> None:
        """Handle button press."""
        await self.coordinator.async_send_command({"cmd": "state", "state": "finish", "group": ""})
