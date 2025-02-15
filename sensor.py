import logging
from datetime import datetime
import requests
import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity

_LOGGER = logging.getLogger(__name__)

# Configuration keys
CONF_API_KEY = "api_key"
CONF_OPERATOR_REF = "operator_ref"
CONF_MONITORING_REF = "monitoring_ref"
CONF_LINE_REF = "line_ref"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_API_KEY): cv.string,
    vol.Required(CONF_OPERATOR_REF): cv.string,
    vol.Required(CONF_MONITORING_REF): cv.string,
    vol.Required(CONF_LINE_REF): cv.string,
})

def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up the MTA Bus Time sensor from YAML configuration."""
    api_key = config.get(CONF_API_KEY)
    operator_ref = config.get(CONF_OPERATOR_REF)
    monitoring_ref = config.get(CONF_MONITORING_REF)
    line_ref = config.get(CONF_LINE_REF)

    add_entities([MTABusSensor(api_key, operator_ref, monitoring_ref, line_ref)], True)

class MTABusSensor(SensorEntity):
    """Representation of an MTA Bus Time sensor configured via YAML."""

    def __init__(self, api_key, operator_ref, monitoring_ref, line_ref):
        self._api_key = api_key
        self._operator_ref = operator_ref
        self._monitoring_ref = monitoring_ref
        self._line_ref = line_ref
        self._state = None
        self._attributes = {}
        self._name = "MTA Bus Arrival"

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the main state (first arrival's Estimated Arrival Time in friendly format)."""
        return self._state

    @property
    def extra_state_attributes(self):
        """Return additional attributes including the list of arrivals and ETA in minutes."""
        return self._attributes

    def update(self):
        """Fetch data from the MTA SIRI API, extract multiple arrivals and update the sensor."""
        url = (
            f"https://bustime.mta.info/api/siri/stop-monitoring.json?"
            f"key={self._api_key}&OperatorRef={self._operator_ref}"
            f"&MonitoringRef={self._monitoring_ref}&LineRef={self._line_ref}"
        )
        try:
            response = requests.get(url, timeout=10)
            if response.status_code != 200:
                _LOGGER.error("HTTP error %s fetching data", response.status_code)
                self._state = "Error"
                return

            data = response.json()
            delivery = (
                data.get("Siri", {})
                    .get("ServiceDelivery", {})
                    .get("StopMonitoringDelivery", [])
            )
            arrivals = []
            if delivery:
                visits = delivery[0].get("MonitoredStopVisit", [])
                for visit in visits:
                    journey = visit.get("MonitoredVehicleJourney", {})
                    call = journey.get("MonitoredCall", {})
                    extensions = call.get("Extensions", {})
                    distances = extensions.get("Distances", {})  # Use this for distance info
                    capacities = extensions.get("Capacities", {})

                    # Raw ISO timestamp strings
                    raw_aat = call.get("AimedArrivalTime")
                    raw_eat = call.get("ExpectedArrivalTime")
                    
                    # Convert raw strings to datetime objects if available
                    aimed_dt = None
                    expected_dt = None
                    if raw_aat:
                        try:
                            aimed_dt = datetime.fromisoformat(raw_aat)
                        except Exception as e:
                            _LOGGER.error("Error parsing AimedArrivalTime: %s", e)
                    if raw_eat:
                        try:
                            expected_dt = datetime.fromisoformat(raw_eat)
                        except Exception as e:
                            _LOGGER.error("Error parsing ExpectedArrivalTime: %s", e)

                    # Format timestamps in a friendly manner
                    aimed_formatted = aimed_dt.strftime("%B %d, %Y at %I:%M %p") if aimed_dt else "Unavailable"
                    expected_formatted = expected_dt.strftime("%B %d, %Y at %I:%M %p") if expected_dt else "Unavailable"

                    # Build a friendly dictionary for this arrival with more readable attribute names
                    arrival = {
                        "Route": journey.get("PublishedLineName"),
                        "Destination": journey.get("DestinationName"),
                        "Current Vehicle Location": journey.get("VehicleLocation"),  # Dict with 'Latitude' and 'Longitude'
                        "Progress Rate": journey.get("ProgressRate"),
                        "Aimed Arrival Time": aimed_formatted,
                        "Estimated Arrival Time": expected_formatted,
                        "Distance": distances.get("PresentableDistance") or "Unavailable",
                        "Distance (m)": distances.get("DistanceFromCall"),
                        "Passenger Count": capacities.get("EstimatedPassengerCount"),
                        "Passenger Capacity": capacities.get("EstimatedPassengerCapacity"),
                        "Stop Name": call.get("StopPointName")
                    }
                    arrivals.append(arrival)
                # Set main sensor state and compute ETA in minutes for the first arrival
                if arrivals:
                    first_arrival = arrivals[0]
                    self._state = first_arrival.get("Estimated Arrival Time")
                    eta_in_minutes = "N/A"
                    if expected_dt:
                        now_dt = datetime.now(expected_dt.tzinfo)
                        delta = expected_dt - now_dt
                        minutes = int(delta.total_seconds() // 60)
                        eta_in_minutes = f"in {minutes} minutes" if minutes >= 0 else "Departed"
                else:
                    self._state = "No arrivals"
            else:
                self._state = "No data"

            # Save attributes: list of arrivals and ETA for the first arrival
            self._attributes = {"Arrivals": arrivals}
            if arrivals:
                self._attributes["ETA in minutes"] = eta_in_minutes

        except Exception as e:
            _LOGGER.error("Error fetching MTA bus data: %s", e)
            self._state = "Error"
