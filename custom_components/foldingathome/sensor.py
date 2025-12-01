"""Sensor platform for Folding@home."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
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
    """Set up sensors."""
    coordinator: FAHDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        FAHStatusSensor(coordinator, entry),
        FAHPPDSensor(coordinator, entry),
        FAHCPUSensor(coordinator, entry),
        FAHWorkUnitsSensor(coordinator, entry),
    ]

    async_add_entities(entities)


class FAHBaseSensor(CoordinatorEntity[FAHDataUpdateCoordinator], SensorEntity):
    """Base class for FAH sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FAHDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize sensor."""
        super().__init__(coordinator)
        self._entry = entry

        info = coordinator.data.get("info", {}) if coordinator.data else {}
        self._machine_id = info.get("id", entry.entry_id)
        self._machine_name = info.get("mach_name", "FAH Client")

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        info = self.coordinator.data.get("info", {}) if self.coordinator.data else {}
        return DeviceInfo(
            identifiers={(DOMAIN, self._machine_id)},
            name=self._machine_name,
            manufacturer="Folding@home",
            model=f"FAH Client {info.get('version', 'Unknown')}",
            sw_version=info.get("version"),
        )


class FAHStatusSensor(FAHBaseSensor):
    """Sensor for folding status."""

    _attr_icon = "mdi:protein"
    _attr_translation_key = "status"

    def __init__(
        self,
        coordinator: FAHDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._machine_id}_status"

    @property
    def native_value(self) -> str:
        """Return status."""
        if not self.coordinator.data:
            return "unknown"
        config = self.coordinator.data.get("config", {})
        if config.get("finish"):
            return "finishing"
        if config.get("paused"):
            return "paused"
        return "folding"


class FAHPPDSensor(FAHBaseSensor):
    """Sensor for points per day."""

    _attr_icon = "mdi:counter"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "PPD"
    _attr_translation_key = "ppd"

    def __init__(
        self,
        coordinator: FAHDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._machine_id}_ppd"

    @property
    def native_value(self) -> int:
        """Return total PPD."""
        if not self.coordinator.data:
            return 0
        units = self.coordinator.data.get("units", [])
        return sum(unit.get("ppd", 0) for unit in units)


class FAHCPUSensor(FAHBaseSensor):
    """Sensor for active CPUs."""

    _attr_icon = "mdi:cpu-64-bit"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "active_cpus"

    def __init__(
        self,
        coordinator: FAHDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._machine_id}_cpus"

    @property
    def native_value(self) -> int:
        """Return active CPU count."""
        if not self.coordinator.data:
            return 0
        config = self.coordinator.data.get("config", {})
        return config.get("cpus", 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        if not self.coordinator.data:
            return {}
        info = self.coordinator.data.get("info", {})
        return {"total_cpus": info.get("cpus", 0)}


class FAHWorkUnitsSensor(FAHBaseSensor):
    """Sensor for work unit count."""

    _attr_icon = "mdi:package-variant"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "work_units"

    def __init__(
        self,
        coordinator: FAHDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._machine_id}_work_units"

    @property
    def native_value(self) -> int:
        """Return WU count."""
        if not self.coordinator.data:
            return 0
        return len(self.coordinator.data.get("units", []))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return WU details."""
        if not self.coordinator.data:
            return {"units": []}
        units = self.coordinator.data.get("units", [])
        return {
            "units": [
                {
                    "project": u.get("assignment", {}).get("project"),
                    "progress": round(u.get("progress", 0) * 100, 1),
                    "state": u.get("state"),
                    "ppd": u.get("ppd", 0),
                }
                for u in units
            ]
        }
