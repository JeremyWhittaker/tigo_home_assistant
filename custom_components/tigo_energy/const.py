"""Constants for the Tigo Energy Cloud integration."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "tigo_energy"
INTEGRATION_VERSION = "0.2.1"
LOGGER = logging.getLogger(__package__)

MANUFACTURER = "Tigo Energy"
MODEL = "Tigo Energy Cloud"
ATTRIBUTION = "Data provided by the Tigo Energy cloud"
CONFIGURATION_URL = "https://ei.tigoenergy.com/fleet/system/overview/index?sysid={}"

CONF_SYSTEM_ID = "system_id"
CONF_SYSTEM_NAME = "system_name"
CONF_TIME_ZONE = "time_zone"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_NIGHT_SCAN_INTERVAL = "night_scan_interval"
CONF_STALE_AFTER = "stale_after"

DEFAULT_SCAN_INTERVAL = 300
DEFAULT_NIGHT_SCAN_INTERVAL = 1800
DEFAULT_STALE_AFTER = 2700
MIN_SCAN_INTERVAL = 120
MAX_SCAN_INTERVAL = 3600

TOPOLOGY_REFRESH_INTERVAL = timedelta(hours=24)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]
