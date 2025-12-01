"""DataUpdateCoordinator for Folding@home."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, WEBSOCKET_PATH, UPDATE_INTERVAL, WEBSOCKET_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class FAHDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for FAH client data."""

    def __init__(self, hass: HomeAssistant, host: str, port: int) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        self.host = host
        self.port = port
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._listen_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._shutdown: bool = False

    @property
    def ws_url(self) -> str:
        """Get WebSocket URL."""
        return f"ws://{self.host}:{self.port}{WEBSOCKET_PATH}"

    @property
    def machine_id(self) -> str | None:
        """Get machine ID from data."""
        if self.data:
            return self.data.get("info", {}).get("id")
        return None

    @property
    def machine_name(self) -> str:
        """Get machine name from data."""
        if self.data:
            return self.data.get("info", {}).get("mach_name", "FAH Client")
        return "FAH Client"

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data - called by coordinator on interval as fallback."""
        # Primary updates come via WebSocket push
        # This is fallback for reconnection scenarios
        if self._ws is None or self._ws.closed:
            await self._connect()
        return self.data or {}

    async def _connect(self) -> None:
        """Establish WebSocket connection."""
        if self._shutdown:
            return

        try:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()

            _LOGGER.debug("Connecting to FAH client at %s", self.ws_url)
            self._ws = await self._session.ws_connect(
                self.ws_url,
                timeout=aiohttp.ClientTimeout(total=WEBSOCKET_TIMEOUT),
            )
            _LOGGER.info("Connected to FAH client at %s", self.ws_url)

            # Wait for initial state
            msg = await self._ws.receive(timeout=WEBSOCKET_TIMEOUT)
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = msg.data
                if data != "ping":
                    try:
                        parsed = json.loads(data)
                        if isinstance(parsed, dict):
                            _LOGGER.debug(
                                "FAH initial state - config: %s, groups: %s",
                                parsed.get("config"),
                                parsed.get("groups"),
                            )
                            self.async_set_updated_data(parsed)
                    except json.JSONDecodeError as err:
                        _LOGGER.warning("Invalid JSON from FAH: %s", err)

            # Start listener task
            if self._listen_task is None or self._listen_task.done():
                self._listen_task = asyncio.create_task(self._listen())

        except asyncio.TimeoutError as err:
            raise UpdateFailed(f"Timeout connecting to FAH client: {err}") from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Failed to connect to FAH client: {err}") from err

    async def _listen(self) -> None:
        """Listen for WebSocket messages."""
        if self._ws is None:
            return

        try:
            async for msg in self._ws:
                if self._shutdown:
                    break

                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = msg.data
                    # Ignore ping messages
                    if data == "ping":
                        continue
                    try:
                        parsed = json.loads(data)
                        # Full state update (dict) vs command response (list)
                        if isinstance(parsed, dict):
                            _LOGGER.debug(
                                "FAH state update - config: %s, groups: %s",
                                parsed.get("config"),
                                parsed.get("groups"),
                            )
                            self.async_set_updated_data(parsed)
                    except json.JSONDecodeError:
                        _LOGGER.warning("Invalid JSON from FAH: %s", data[:100])
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    _LOGGER.error("WebSocket error: %s", self._ws.exception())
                    break
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    _LOGGER.debug("WebSocket closed")
                    break
        except asyncio.CancelledError:
            _LOGGER.debug("WebSocket listener cancelled")
            raise
        except Exception as err:
            _LOGGER.error("WebSocket listener error: %s", err)
        finally:
            await self._disconnect()
            # Schedule reconnection if not shutting down
            if not self._shutdown:
                self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        """Schedule a reconnection attempt."""
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._reconnect())

    async def _reconnect(self) -> None:
        """Attempt to reconnect after a delay."""
        await asyncio.sleep(UPDATE_INTERVAL)
        if not self._shutdown:
            try:
                await self._connect()
            except UpdateFailed as err:
                _LOGGER.warning("Reconnection failed: %s", err)
                self._schedule_reconnect()

    async def _disconnect(self) -> None:
        """Disconnect WebSocket."""
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        self._ws = None

    async def async_shutdown(self) -> None:
        """Shutdown coordinator."""
        self._shutdown = True

        if self._listen_task is not None:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass

        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass

        await self._disconnect()

        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def async_send_command(self, command: dict[str, Any]) -> None:
        """Send command to FAH client.

        Commands should be dicts like:
            {"cmd": "state", "state": "pause"}
            {"cmd": "state", "state": "fold"}
            {"cmd": "state", "state": "finish"}
        """
        if self._ws is None or self._ws.closed:
            try:
                await self._connect()
            except UpdateFailed as err:
                _LOGGER.error("Cannot send command, connection failed: %s", err)
                return

        if self._ws is not None and not self._ws.closed:
            try:
                await self._ws.send_str(json.dumps(command))
                _LOGGER.debug("Sent command to FAH: %s", command)
            except aiohttp.ClientError as err:
                _LOGGER.error("Failed to send command: %s", err)
