"""Constants for Folding@home integration."""
from typing import Final

DOMAIN: Final = "foldingathome"
DEFAULT_PORT: Final = 7396
WEBSOCKET_PATH: Final = "/api/websocket"

# Config keys
CONF_HOST: Final = "host"
CONF_PORT: Final = "port"

# State values from FAH
STATE_RUN: Final = "RUN"
STATE_PAUSE: Final = "PAUSE"
STATE_FINISH: Final = "FINISH"
STATE_WAIT: Final = "WAIT"

# Update interval for coordinator fallback (WebSocket is primary)
UPDATE_INTERVAL: Final = 30

# Connection settings
WEBSOCKET_TIMEOUT: Final = 10
RECONNECT_INTERVAL: Final = 30
