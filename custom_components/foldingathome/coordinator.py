"""DataUpdateCoordinator for Folding@home."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, WEBSOCKET_PATH, UPDATE_INTERVAL, WEBSOCKET_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class FAHDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for FAH client data."""

    def __init__(self, hass: HomeAssistant, host: str, port: int) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{host}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        self.host = host
        self.port = port
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._listen_task: asyncio.Task | None = None
        self._shutdown: bool = False
        self._connected: bool = False
        self._reconnect_delay: int = 10  # Start with 10 seconds

    @property
    def ws_url(self) -> str:
        """Get WebSocket URL."""
        return f"ws://{self.host}:{self.port}{WEBSOCKET_PATH}"

    @property
    def machine_id(self) -> str | None:
        """Get machine ID from data."""
        if self.data:
            info = self.data.get("info") or {}
            return info.get("id")
        return None

    @property
    def machine_name(self) -> str:
        """Get machine name from data."""
        if self.data:
            info = self.data.get("info") or {}
            return info.get("mach_name", "FAH Client")
        return "FAH Client"

    async def async_initialize(self) -> None:
        """Initialize the coordinator - try to connect but don't fail if offline."""
        _LOGGER.info("Initializing FAH coordinator for %s", self.host)
        try:
            await self._connect()
        except Exception as err:
            _LOGGER.warning(
                "FAH client %s not available at startup: %s. Will keep trying.",
                self.host, err
            )
            # Start reconnection loop
            self._schedule_reconnect()

    def _apply_incremental_update(self, update: list) -> None:
        """Apply an incremental update to the stored state."""
        if not self.data or len(update) < 2:
            return

        new_data = dict(self.data)
        path = update[:-1]
        value = update[-1]

        current = new_data
        for i, key in enumerate(path[:-1]):
            if isinstance(current, dict):
                if key not in current or current[key] is None:
                    current[key] = {}
                if isinstance(current[key], dict):
                    current[key] = dict(current[key])
                current = current[key]
            elif isinstance(current, list):
                idx = int(key) if isinstance(key, (int, str)) and str(key).isdigit() else key
                if isinstance(idx, int) and 0 <= idx < len(current):
                    if current[idx] is None:
                        current[idx] = {}
                    if isinstance(current[idx], dict):
                        current[idx] = dict(current[idx])
                    current = current[idx]
                else:
                    return
            else:
                return

        final_key = path[-1]
        if isinstance(current, dict):
            current[final_key] = value
            if path == ["groups", "", "config", "paused"]:
                _LOGGER.info("FAH %s paused changed to: %s", self.host, value)
            elif path == ["groups", "", "config", "finish"]:
                _LOGGER.info("FAH %s finish changed to: %s", self.host, value)
            self.async_set_updated_data(new_data)
        elif isinstance(current, list) and isinstance(final_key, int):
            if 0 <= final_key < len(current):
                current[final_key] = value
                self.async_set_updated_data(new_data)

    async def _async_update_data(self) -> dict[str, Any]:
        """Called by HA coordinator on interval - just return current data."""
        # Don't try to connect here - let the listener/reconnect handle it
        # This prevents two things trying to connect at once
        return self.data or {}

    async def _connect(self) -> None:
        """Establish WebSocket connection."""
        if self._shutdown:
            return

        # Clean up any existing connection first
        await self._cleanup()

        _LOGGER.info("Connecting to FAH client at %s", self.ws_url)

        self._session = aiohttp.ClientSession()
        try:
            self._ws = await self._session.ws_connect(
                self.ws_url,
                timeout=aiohttp.ClientTimeout(total=WEBSOCKET_TIMEOUT),
            )

            # Wait for initial state
            msg = await self._ws.receive(timeout=WEBSOCKET_TIMEOUT)
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = msg.data
                if data != "ping":
                    parsed = json.loads(data)
                    if parsed is not None and isinstance(parsed, dict):
                        self.async_set_updated_data(parsed)
                        _LOGGER.info("Connected to FAH %s, received initial state", self.host)
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                raise aiohttp.ClientError(f"WebSocket closed immediately: {msg.type}")

            # Connection successful
            self._connected = True
            self._reconnect_delay = 10  # Reset backoff on success

            # Start listener task
            if self._listen_task is None or self._listen_task.done():
                self._listen_task = self.hass.async_create_background_task(
                    self._listen(), f"fah_listener_{self.host}"
                )

        except Exception as err:
            self._connected = False
            await self._cleanup()
            raise

    async def _listen(self) -> None:
        """Listen for WebSocket messages."""
        if self._ws is None:
            return

        _LOGGER.debug("FAH WebSocket listener started for %s", self.host)
        try:
            async for msg in self._ws:
                if self._shutdown:
                    break

                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = msg.data
                    if data == "ping":
                        continue
                    try:
                        parsed = json.loads(data)
                        if parsed is None:
                            continue
                        if isinstance(parsed, dict):
                            self.async_set_updated_data(parsed)
                        elif isinstance(parsed, list) and len(parsed) >= 2:
                            self._apply_incremental_update(parsed)
                    except json.JSONDecodeError:
                        _LOGGER.warning("Invalid JSON from FAH %s", self.host)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    _LOGGER.error("WebSocket error from %s: %s", self.host, self._ws.exception())
                    break
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                    _LOGGER.info("WebSocket to %s closed", self.host)
                    break

        except asyncio.CancelledError:
            _LOGGER.debug("WebSocket listener for %s cancelled", self.host)
            raise
        except Exception as err:
            _LOGGER.error("WebSocket listener error for %s: %s", self.host, err)
        finally:
            self._connected = False
            await self._cleanup()
            if not self._shutdown:
                _LOGGER.info("FAH %s disconnected, will reconnect", self.host)
                self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        """Schedule a reconnection attempt."""
        if self._shutdown:
            return

        _LOGGER.info("FAH %s: reconnecting in %d seconds", self.host, self._reconnect_delay)
        self.hass.async_create_background_task(
            self._reconnect(), f"fah_reconnect_{self.host}"
        )

    async def _reconnect(self) -> None:
        """Attempt to reconnect after a delay."""
        await asyncio.sleep(self._reconnect_delay)

        if self._shutdown:
            return

        # Increase delay for next time (exponential backoff, max 5 min)
        self._reconnect_delay = min(self._reconnect_delay * 2, 300)

        try:
            await self._connect()
        except Exception as err:
            _LOGGER.warning("FAH %s reconnection failed: %s", self.host, err)
            if not self._shutdown:
                self._schedule_reconnect()

    async def _cleanup(self) -> None:
        """Clean up WebSocket and session."""
        if self._ws is not None:
            try:
                if not self._ws.closed:
                    await self._ws.close()
            except Exception:
                pass
            self._ws = None

        if self._session is not None:
            try:
                if not self._session.closed:
                    await self._session.close()
            except Exception:
                pass
            self._session = None

    async def async_shutdown(self) -> None:
        """Shutdown coordinator."""
        _LOGGER.info("Shutting down FAH coordinator for %s", self.host)
        self._shutdown = True

        if self._listen_task is not None and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass

        await self._cleanup()

    async def async_send_command(self, command: dict[str, Any]) -> None:
        """Send command to FAH client."""
        _LOGGER.info("Sending command to FAH %s: %s (connected: %s)", self.host, command, self._connected)

        # Reset backoff - user wants action now
        self._reconnect_delay = 10

        # If not connected, try to connect first
        if not self._connected or self._ws is None or self._ws.closed:
            _LOGGER.info("FAH %s: not connected, attempting connection for command", self.host)
            try:
                await self._connect()
            except Exception as err:
                _LOGGER.error("FAH %s: cannot send command, connection failed: %s", self.host, err)
                self._schedule_reconnect()
                return

        # Try to send
        if self._ws is not None and not self._ws.closed:
            try:
                await self._ws.send_str(json.dumps(command))
                _LOGGER.info("Sent command to FAH %s: %s", self.host, command)
            except Exception as err:
                _LOGGER.error("FAH %s: failed to send command: %s", self.host, err)
                self._connected = False
                self._schedule_reconnect()
