"""Constants for the Login Monitor integration."""

DOMAIN = "login_monitor"

# Event fired on the bus when a successful authenticated access is detected.
EVENT_LOGIN = "login_monitor_login"

# Config / options keys.
CONF_NEW_IP_ONLY = "new_ip_only"
CONF_IGNORE_SYSTEM_TOKENS = "ignore_system_tokens"
CONF_GEO_LOOKUP = "geo_lookup"

# Defaults.
DEFAULT_NEW_IP_ONLY = True
DEFAULT_IGNORE_SYSTEM_TOKENS = True
# Off by default: enabling it sends the source IP to a third-party geo service.
DEFAULT_GEO_LOOKUP = False

# Keyless GeoIP provider (HTTPS, supports IPv6). {ip} is substituted.
GEO_PROVIDER_URL = "https://ipwho.is/{ip}"
GEO_TIMEOUT = 5
