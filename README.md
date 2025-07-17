# MTA Bus Time

Custom component for Home Assistant that displays upcoming bus arrivals using the MTA Bus Time API.

## Installation

1. In HACS, add this repository as a custom repository of type `integration`.
2. Install **MTA Bus Time** from the HACS integrations list.
3. Restart Home Assistant after installation.

## Configuration

Example configuration in `configuration.yaml`:

```yaml
sensor:
  - platform: mta_bus_time
    api_key: YOUR_API_KEY
    operator_ref: MTA
    line_ref: MTA NYCT
    departures:
      - name: My Stop
        monitoring_ref: 123456
        route: M5
```

Each entry under `departures` represents a stop to monitor.
