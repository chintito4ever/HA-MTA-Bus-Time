import datetime
import logging
import requests
import voluptuous as vol
from dateutil import parser  # Added for robust ISO 8601 parsing

from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity
import homeassistant.helpers.config_validation as cv
from homeassistant.util import Throttle

_LOGGER = logging.getLogger(__name__)

# Configuration keys
CONF_API_KEY = "api_key"
CONF_OPERATOR_REF = "operator_ref"
CONF_LINE_REF = "line_ref"  # default if departure doesn't override
CONF_DEPARTURES = "departures"

# Each departure should have a name and monitoring_ref, and can optionally specify a route.
DEPARTURE_SCHEMA = vol.Schema({
    vol.Required("name"): cv.string,
    vol.Required("monitoring_ref"): cv.string,
    vol.Optional("route"): cv.string,
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_API_KEY): cv.string,
    vol.Required(CONF_OPERATOR_REF): cv.string,
    vol.Required(CONF_LINE_REF): cv.string,
    vol.Optional(CONF_DEPARTURES, default=[]): vol.All(cv.ensure_list, [DEPARTURE_SCHEMA])
})

MIN_TIME_BETWEEN_UPDATES = datetime.timedelta(seconds=60)

class MTAData:
    """Handles fetching data for multiple departures."""

    def __init__(self, api_key, operator_ref, departures):
        self._api_key = api_key
        self._operator_ref = operator_ref
        self._departures = departures  # List of departure dicts
        self.info = {}  # Dictionary mapping departure name -> list of arrivals

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def update(self):
        """Fetch data for all departures."""
        new_info = {}
        base_url = "https://bustime.mta.info/api/siri/stop-monitoring.json?"
        for dep in self._departures:
            dep_name = dep["name"]
            monitoring_ref = dep["monitoring_ref"]
            route = dep.get("route")
            url = f"{base_url}key={self._api_key}&OperatorRef={self._operator_ref}&MonitoringRef={monitoring_ref}"
            if route:
                url += f"&LineRef={route}"
            try:
                response = requests.get(url, timeout=10)
                if response.status_code != 200:
                    _LOGGER.error("HTTP error %s fetching data for %s", response.status_code, dep_name)
                    new_info[dep_name] = []
                    continue

                data = response.json()
                delivery = (data.get("Siri", {})
                              .get("ServiceDelivery", {})
                              .get("StopMonitoringDelivery", []))
                arrivals = []
                if delivery:
                    visits = delivery[0].get("MonitoredStopVisit", [])
                    for visit in visits:
                        journey = visit.get("MonitoredVehicleJourney", {})
                        call = journey.get("MonitoredCall", {})
                        extensions = call.get("Extensions", {})
                        distances = extensions.get("Distances", {})
                        capacities = extensions.get("Capacities", {})

                        raw_aat = call.get("AimedArrivalTime")
                        raw_eat = call.get("ExpectedArrivalTime")
                        aimed_dt = None
                        expected_dt = None
                        if raw_aat:
                            try:
                                aimed_dt = parser.parse(raw_aat)
                            except Exception as e:
                                _LOGGER.error("Error parsing AimedArrivalTime for %s: %s", dep_name, e)
                        if raw_eat:
                            try:
                                expected_dt = parser.parse(raw_eat)
                            except Exception as e:
                                _LOGGER.error("Error parsing ExpectedArrivalTime for %s: %s", dep_name, e)

                        aimed_formatted = aimed_dt.strftime("%B %d, %Y at %I:%M %p") if aimed_dt else "Unavailable"
                        expected_formatted = expected_dt.strftime("%B %d, %Y at %I:%M %p") if expected_dt else "Unavailable"

                        arrival = {
                            "Route": journey.get("PublishedLineName"),
                            "Destination": journey.get("DestinationName"),
                            "Current Vehicle Location": journey.get("VehicleLocation"),
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
                new_info[dep_name] = arrivals
            except Exception as e:
                _LOGGER.error("Error fetching data for %s: %s", dep_name, e)
                new_info[dep_name] = []
        self.info = new_info

# The rest of your code remains the same.
class MTABusStopSensor(SensorEntity):
    """Sensor for a specific departure/stop."""

    def __init__(self, data, departure):
        """Initialize sensor for a single departure."""
        self.data = data
        self._dep_config = departure
        self._dep_name = departure["name"]
        self._attr_name = f"MTA Arrival - {self._dep_name}"
        self._attr_icon = "mdi:bus"
        self._state = None
        self._attributes = {}

    @property
    def state(self):
        """Return the first arrival's Estimated Arrival Time for this departure."""
        arrivals = self.data.info.get(self._dep_name, [])
        if arrivals and arrivals[0].get("Estimated Arrival Time") != "Unavailable":
            return arrivals[0].get("Estimated Arrival Time")
        return "No arrivals"

    @property
    def extra_state_attributes(self):
        """Return additional attributes including all arrivals and ETA info."""
        arrivals = self.data.info.get(self._dep_name, [])
        attrs = {"Arrivals": arrivals, "Monitoring Ref": self._dep_config.get("monitoring_ref")}
        eta_str = "N/A"
        if arrivals and arrivals[0].get("Estimated Arrival Time") != "Unavailable":
            try:
                expected_dt = datetime.datetime.strptime(
                    arrivals[0].get("Estimated Arrival Time"), "%B %d, %Y at %I:%M %p"
                )
                now_dt = datetime.datetime.now(expected_dt.tzinfo)
                delta = expected_dt - now_dt
                minutes = int(delta.total_seconds() // 60)
                eta_str = f"in {minutes} minutes" if minutes >= 0 else "Departed"
            except Exception as e:
                _LOGGER.error("Error computing ETA for %s: %s", self._dep_name, e)
                eta_str = "N/A"
        attrs["ETA in minutes"] = eta_str
        # Add new attribute "Arrives" with the same value (or you can adjust the formatting)
        attrs["Arrives"] = eta_str
        return attrs


    def update(self):
        """Update sensor state from the shared data."""
        self.data.update()
        arrivals = self.data.info.get(self._dep_name, [])
        if arrivals and arrivals[0].get("Estimated Arrival Time") != "Unavailable":
            self._state = arrivals[0].get("Estimated Arrival Time")
        else:
            self._state = "No arrivals"
        self._attributes = self.extra_state_attributes

def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up MTA Bus Time sensors via YAML configuration."""
    api_key = config.get(CONF_API_KEY)
    operator_ref = config.get(CONF_OPERATOR_REF)
    departures = config.get(CONF_DEPARTURES)

    # Create a shared data object that will fetch data for all departures.
    data = MTAData(api_key, operator_ref, departures)
    data.update()  # Initial update

    sensors = []
    for dep in departures:
        sensors.append(MTABusStopSensor(data, dep))
    add_entities(sensors, True)
