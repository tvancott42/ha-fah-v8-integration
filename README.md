# Folding@home v8 Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Validate](https://github.com/yourgithub/ha-fah-v8/actions/workflows/validate.yaml/badge.svg)](https://github.com/yourgithub/ha-fah-v8/actions/workflows/validate.yaml)

A Home Assistant custom integration for monitoring and controlling [Folding@home](https://foldingathome.org/) v8.x clients via their local WebSocket API.

## Features

- **Real-time monitoring** via WebSocket push updates
- **Status sensor** - Shows current state (folding, paused, finishing)
- **Points Per Day (PPD) sensor** - Track your contribution
- **Active CPUs sensor** - Monitor resource usage
- **Work Units sensor** - Track active work units with progress details
- **Folding switch** - Pause/resume folding
- **Finish & Pause button** - Complete current work units then pause

## Requirements

- Home Assistant 2024.1.0 or newer
- Folding@home v8.x client (codename "Bastet")
- Network access to the FAH client's WebSocket API (default port 7396)

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu and select "Custom repositories"
3. Add this repository URL with category "Integration"
4. Search for "Folding@home v8" and install
5. Restart Home Assistant

### Manual Installation

1. Download the `custom_components/foldingathome` folder from this repository
2. Copy it to your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant

## Configuration

### FAH Client Setup

Ensure your Folding@home client allows connections from Home Assistant:

```bash
# Linux: Start with network access flags
fah-client --allow='127.0.0.1 192.168.0.0/24' --deny=0/0 --http-addresses=0.0.0.0:7396
```

Or edit `/etc/fah-client/config.xml` to include appropriate allow/deny rules.

### Home Assistant Setup

1. Go to **Settings** > **Devices & Services**
2. Click **Add Integration**
3. Search for "Folding@home"
4. Enter your FAH client's hostname/IP and port (default: 7396)

## Entities

### Sensors

| Entity | Description |
|--------|-------------|
| Status | Current state: `folding`, `paused`, or `finishing` |
| Points Per Day | Total PPD across all work units |
| Active CPUs | Number of CPUs allocated to folding |
| Work Units | Number of active work units (with details in attributes) |

### Controls

| Entity | Description |
|--------|-------------|
| Folding (switch) | Toggle to pause/resume folding |
| Finish & Pause (button) | Complete current work units then pause |

## Example Automations

### Pause folding during peak hours

```yaml
automation:
  - alias: "Pause FAH during peak hours"
    trigger:
      - platform: time
        at: "17:00:00"
    action:
      - service: switch.turn_off
        target:
          entity_id: switch.servername_folding

  - alias: "Resume FAH after peak hours"
    trigger:
      - platform: time
        at: "22:00:00"
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.servername_folding
```

### Notify on high PPD

```yaml
automation:
  - alias: "Notify high PPD"
    trigger:
      - platform: numeric_state
        entity_id: sensor.servername_ppd
        above: 1000000
    action:
      - service: notify.mobile_app
        data:
          message: "FAH PPD exceeded 1 million!"
```

## Troubleshooting

### Cannot connect to FAH client

1. Verify the FAH client is running: `systemctl status fah-client`
2. Check the client is listening on the expected port: `netstat -tlnp | grep 7396`
3. Ensure firewall allows connections from Home Assistant
4. Verify the client's `--allow` configuration includes Home Assistant's IP

### Connection drops frequently

The integration automatically reconnects on connection loss. If issues persist:

1. Check network stability between Home Assistant and FAH client
2. Review Home Assistant logs for specific error messages
3. Ensure the FAH client isn't being restarted frequently

## Development

### Testing WebSocket Connection

```python
import asyncio
import aiohttp
import json

async def test():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect('ws://localhost:7396/api/websocket') as ws:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    if msg.data != "ping":
                        print(json.dumps(json.loads(msg.data), indent=2))
                        break

asyncio.run(test())
```

## License

This project is licensed under the GPL-3.0 License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- [Folding@home](https://foldingathome.org/) for their distributed computing platform
- [fah-client-bastet](https://github.com/FoldingAtHome/fah-client-bastet) for the v8 client
