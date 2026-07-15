"""Constants for the Login Monitor integration."""

DOMAIN = "login_monitor"

# Event fired on the bus when a successful authenticated access is detected.
EVENT_LOGIN = "login_monitor_login"

# Config / options keys.
CONF_NEW_IP_ONLY = "new_ip_only"
CONF_IGNORE_SYSTEM_TOKENS = "ignore_system_tokens"

# Defaults.
DEFAULT_NEW_IP_ONLY = True
DEFAULT_IGNORE_SYSTEM_TOKENS = True
