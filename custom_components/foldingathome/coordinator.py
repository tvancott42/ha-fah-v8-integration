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
        self._reconnect_attempts: int = 0
        self._max_reconnect_delay: int = 300  # 5 minutes max
        self._connected: bool = False

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

    def _apply_incremental_update(self, update: list) -> None:
        """Apply an incremental update to the stored state.

        Updates come as arrays like: ["groups", "", "config", "paused", false]
        This represents: state["groups"][""]["config"]["paused"] = false
        """
        if not self.data or len(update) < 2:
            return

        # Make a shallow copy of data to modify
        new_data = dict(self.data)
        path = update[:-1]  # All but last element is the path
        value = update[-1]  # Last element is the value

        # Navigate to the parent of the target key
        current = new_data
        for i, key in enumerate(path[:-1]):
            if isinstance(current, dict):
                if key not in current or current[key] is None:
                    current[key] = {}
                # Make a copy at each level to avoid mutating original
                current[key] = dict(current[key]) if isinstance(current[key], dict) else current[key]
                current = current[key]
            elif isinstance(current, list):
                # Handle list indices (e.g., ["units", 0, "ppd", 123])
                idx = int(key) if isinstance(key, (int, str)) and str(key).isdigit() else key
                if isinstance(idx, int) and 0 <= idx < len(current):
                    if current[idx] is None:
                        current[idx] = {}
                    if isinstance(current[idx], dict):
                        current[idx] = dict(current[idx])
                    current = current[idx]
                else:
                    _LOGGER.debug("FAH update path invalid at index %s: %s", key, update)
                    return
            else:
                _LOGGER.debug("FAH update path not navigable at %s: %s", key, update)
                return

        # Set the final value
        final_key = path[-1]
        if isinstance(current, dict):
            current[final_key] = value
            # Log state changes for important fields
            if path == ["groups", "", "config", "paused"]:
                _LOGGER.info("FAH paused changed to: %s", value)
            elif path == ["groups", "", "config", "finish"]:
                _LOGGER.info("FAH finish changed to: %s", value)

            self.async_set_updated_data(new_data)
        elif isinstance(current, list) and isinstance(final_key, int):
            if 0 <= final_key < len(current):
                current[final_key] = value
                self.async_set_updated_data(new_data)
        else:
            _LOGGER.debug("FAH could not apply update: %s", update)

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
            # Close existing session to ensure clean state
            await self._cleanup_session()

            self._session = aiohttp.ClientSession()

            _LOGGER.info("Connecting to FAH client at %s (attempt %d)", self.ws_url, self._reconnect_attempts + 1)
            self._ws = await self._session.ws_connect(
                self.ws_url,
                timeout=aiohttp.ClientTimeout(total=WEBSOCKET_TIMEOUT),
            )

            # Wait for initial state
            msg = await self._ws.receive(timeout=WEBSOCKET_TIMEOUT)
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = msg.data
                if data != "ping":
                    try:
                        parsed = json.loads(data)
                        if parsed is not None and isinstance(parsed, dict):
                            self.async_set_updated_data(parsed)
                            _LOGGER.info("Connected to FAH client at %s, received initial state", self.ws_url)
                    except json.JSONDecodeError as err:
                        _LOGGER.warning("Invalid JSON from FAH: %s", err)
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                raise aiohttp.ClientError(f"WebSocket closed immediately: {msg.type}")

            # Connection successful - reset reconnect counter
            self._reconnect_attempts = 0
            self._connected = True

            # Start listener task as background task so it doesn't block HA startup
            if self._listen_task is None or self._listen_task.done():
                self._listen_task = self.hass.async_create_background_task(
                    self._listen(), f"fah_listener_{self.host}"
                )

        except asyncio.TimeoutError as err:
            self._connected = False
            raise UpdateFailed(f"Timeout connecting to FAH client: {err}") from err
        except aiohttp.ClientError as err:
            self._connected = False
            raise UpdateFailed(f"Failed to connect to FAH client: {err}") from err
        except Exception as err:
            self._connected = False
            _LOGGER.exception("Unexpected error connecting to FAH client")
            raise UpdateFailed(f"Unexpected error connecting to FAH client: {err}") from err

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
                    # Ignore ping messages
                    if data == "ping":
                        continue
                    try:
                        parsed = json.loads(data)
                        if parsed is None:
                            # Server sent null, likely during shutdown/reconnect
                            _LOGGER.debug("FAH %s sent null message, ignoring", self.host)
                            continue
                        if isinstance(parsed, dict):
                            # Full state update
                            groups = parsed.get("groups") or {}
                            default_group = groups.get("") or {}
                            group_config = default_group.get("config") or {}
                            _LOGGER.debug(
                                "FAH %s full state - paused: %s, finish: %s",
                                self.host,
                                group_config.get("paused"),
                                group_config.get("finish"),
                            )
                            self.async_set_updated_data(parsed)
                        elif isinstance(parsed, list) and len(parsed) >= 2:
                            # Incremental update: ["path", "to", "key", value]
                            self._apply_incremental_update(parsed)
                    except json.JSONDecodeError:
                        _LOGGER.warning("Invalid JSON from FAH %s: %s", self.host, data[:100])
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    ws_exception = self._ws.exception() if self._ws else None
                    _LOGGER.error("WebSocket error from %s: %s", self.host, ws_exception)
                    break
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    _LOGGER.info("WebSocket connection to %s closed", self.host)
                    break
        except asyncio.CancelledError:
            _LOGGER.debug("WebSocket listener for %s cancelled", self.host)
            raise
        except Exception as err:
            _LOGGER.error("WebSocket listener error for %s: %s", self.host, err)
        finally:
            _LOGGER.info("FAH client %s disconnected, will attempt reconnection", self.host)
            await self._disconnect()
            # Schedule reconnection if not shutting down
            if not self._shutdown:
                self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        """Schedule a reconnection attempt."""
        if self._shutdown:
            return
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = self.hass.async_create_background_task(
                self._reconnect(), f"fah_reconnect_{self.host}"
            )

    async def _reconnect(self) -> None:
        """Attempt to reconnect after a delay with exponential backoff."""
        # Calculate delay with exponential backoff: 10s, 20s, 40s, 80s, ... up to max
        base_delay = 10
        delay = min(base_delay * (2 ** self._reconnect_attempts), self._max_reconnect_delay)
        self._reconnect_attempts += 1

        _LOGGER.info(
            "FAH client %s: scheduling reconnect in %d seconds (attempt %d)",
            self.host, delay, self._reconnect_attempts
        )
        await asyncio.sleep(delay)

        if self._shutdown:
            return

        try:
            await self._connect()
        except UpdateFailed as err:
            _LOGGER.warning("Reconnection to %s failed: %s", self.host, err)
            self._schedule_reconnect()
        except Exception as err:
            _LOGGER.exception("Unexpected error during reconnection to %s: %s", self.host, err)
            self._schedule_reconnect()

    async def _disconnect(self) -> None:
        """Disconnect WebSocket."""
        self._connected = False
        if self._ws is not None and not self._ws.closed:
            try:
                await self._ws.close()
            except Exception:
                pass  # Ignore errors during close
        self._ws = None

    async def _cleanup_session(self) -> None:
        """Clean up existing session and websocket."""
        await self._disconnect()
        if self._session is not None and not self._session.closed:
            try:
                await self._session.close()
            except Exception:
                pass  # Ignore errors during close
        self._session = None

    async def async_shutdown(self) -> None:
        """Shutdown coordinator."""
        _LOGGER.info("Shutting down FAH coordinator for %s", self.host)
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

        await self._cleanup_session()

    async def async_send_command(self, command: dict[str, Any]) -> None:
        """Send command to FAH client.

        Commands should be dicts like:
            {"cmd": "state", "state": "pause"}
            {"cmd": "state", "state": "fold"}
            {"cmd": "state", "state": "finish"}
        """
        _LOGGER.info("Sending command to FAH %s: %s (connected: %s)", self.host, command, self._connected)

        # Reset backoff on user action - they want control now
        self._reconnect_attempts = 0

        # Cancel any pending reconnect task since we're connecting now
        if self._reconnect_task is not None and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None

        # Try to send, reconnecting if necessary
        for attempt in range(2):
            if self._ws is None or self._ws.closed:
                _LOGGER.info("FAH %s: WebSocket not connected, attempting to connect...", self.host)
                try:
                    await self._connect()
                except UpdateFailed as err:
                    _LOGGER.error("Cannot send command to %s, connection failed: %s", self.host, err)
                    return

            if self._ws is not None:
                try:
                    await self._ws.send_str(json.dumps(command))
                    _LOGGER.info("Sent command to FAH %s: %s", self.host, command)
                    return
                except (aiohttp.ClientError, ConnectionResetError) as err:
                    _LOGGER.warning("Failed to send command to %s (attempt %d): %s", self.host, attempt + 1, err)
                    # Force reconnect on next attempt
                    await self._disconnect()
                    if attempt == 0:
                        continue
                    _LOGGER.error("Failed to send command to %s after retry: %s", self.host, err)
