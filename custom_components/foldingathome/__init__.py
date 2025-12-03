"""Folding@home integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import FAHDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH, Platform.BUTTON]


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old entry data to new format."""
    if "machine_id" not in entry.data:
        # Use entry.unique_id which was set to machine_id during config flow
        if entry.unique_id:
            new_data = {**entry.data, "machine_id": entry.unique_id}
            hass.config_entries.async_update_entry(entry, data=new_data)
            _LOGGER.info("Migrated FAH entry %s with machine_id %s", entry.title, entry.unique_id)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Folding@home from config entry."""
    # Ensure migration happens
    await async_migrate_entry(hass, entry)

    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]

    _LOGGER.info("Setting up FAH integration for %s:%s", host, port)
    coordinator = FAHDataUpdateCoordinator(hass, host, port)

    # Don't block setup on connection - let coordinator handle reconnection
    # This allows HA to start even if FAH clients are offline
    await coordinator.async_initialize()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    _LOGGER.debug("Forwarding platform setups for %s", host)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info("FAH integration setup complete for %s (connected: %s)", host, coordinator._connected)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: FAHDataUpdateCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()

    return unload_ok
