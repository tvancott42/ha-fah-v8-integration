"""Config flow for Folding@home integration."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN, DEFAULT_PORT, WEBSOCKET_PATH, WEBSOCKET_TIMEOUT

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
    }
)


class FAHConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for Folding@home."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle user step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input.get(CONF_PORT, DEFAULT_PORT)

            # Test connection
            try:
                machine_info = await self._test_connection(host, port)

                # Use machine ID as unique identifier
                machine_id = machine_info.get("id")
                if machine_id:
                    await self.async_set_unique_id(machine_id)
                    self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=machine_info.get("mach_name", host),
                    data={CONF_HOST: host, CONF_PORT: port},
                )
            except asyncio.TimeoutError:
                errors["base"] = "timeout"
            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during config flow")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def _test_connection(self, host: str, port: int) -> dict[str, Any]:
        """Test connection and return machine info."""
        url = f"ws://{host}:{port}{WEBSOCKET_PATH}"

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                url,
                timeout=aiohttp.ClientTimeout(total=WEBSOCKET_TIMEOUT),
            ) as ws:
                msg = await ws.receive(timeout=WEBSOCKET_TIMEOUT)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    return data.get("info") or {}

        raise Exception("No data received from FAH client")
