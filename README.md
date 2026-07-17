# Login Monitor (Home Assistant custom integration)

Home Assistant is silent on **successful** logins (no log line, no event, no
notification), and while it does surface **failed** logins, it only does so as a
hardcoded persistent notification carrying the source IP and nothing else. This
integration closes both gaps and fires bus events **in real time** (enriched
with approximate location) for successful *and* failed logins.

For successful logins it wraps `AuthManager.async_create_access_token`, the
single point where every authenticated session stamps its source IP, firing an
event the first time a given refresh token is used. That covers a genuine new
login or a new-device session; later access-token mints for the same refresh
token (routine refreshes, roughly every 30 minutes while a session is active)
do not fire again, no matter how often the source IP changes.

For failed logins it wraps `http.ban.process_wrong_login`, the single point
every invalid-auth request is funnelled through, firing an event the moment a
login fails.

**Safety:** the wrapper forwards its arguments unchanged and returns the
original result *first*; the event fire is fully guarded in `try/except`. A bug
in this integration therefore cannot break authentication: the worst case is a
missed notification while logins keep working. If a future Home Assistant
release renames that method, this integration simply fails to load (auth
untouched).

**Safety (failed logins):** the failed-login wrapper awaits Home Assistant's
real `process_wrong_login` *first* and returns its result unchanged; our event
fire is fully guarded, so a bug here cannot affect failed-login handling or the
IP ban logic.

No reading `.storage/auth`, no removed APIs, no polling.

## Requirements

Home Assistant 2024.1 or newer. The bundled brand icon is served locally and
only appears on HA 2026.3+ (older versions work fine, just without the icon).

## The events

### Successful logins: `login_monitor_login`

| Field | Example | Notes |
|-------|---------|-------|
| `user_name` | `Diego` | HA user the token belongs to |
| `user_id` | `abcd...` | |
| `client` | `iOS app` / `Web UI` / `https://...` | friendly label for the client (see below) |
| `client_id` | `https://home-assistant.io/iOS` | raw OAuth client id |
| `client_name` | `null` (usually) | only set for named long-lived tokens |
| `token_type` | `normal` / `long_lived_access_token` | |
| `ip_address` | `73.12.x.x` | source IP of the access |
| `location` | `Denver, Colorado, United States` | approximate location, only when geo lookup is enabled |
| `city` / `region` / `country` | `Denver` / `Colorado` / `United States` | individual geo fields (geo lookup only) |
| `country_code` | `US` | ISO 3166-1 alpha-2 country code (geo lookup only) |

### Failed logins: `login_monitor_failed_login`

Fired the first time each distinct source IP fails a login / invalid-auth
request. Failures are deduplicated by IP for the life of the Home Assistant
process, so a public instance hit by bots does not spam you (the counter resets
on restart, so an IP that failed before a restart can notify once more after).

| Field | Example | Notes |
|-------|---------|-------|
| `ip_address` | `185.220.x.x` | source IP of the failed attempt |
| `is_new_ip` | `true` | always true, the event only fires for new IPs |
| `location` | `Amsterdam, North Holland, Netherlands` | approximate location, only when geo lookup is enabled |
| `city` / `region` / `country` | `Amsterdam` / `North Holland` / `Netherlands` | individual geo fields (geo lookup only) |
| `country_code` | `NL` | ISO 3166-1 alpha-2 country code (geo lookup only) |

Note: `process_wrong_login` fires for *any* invalid-auth request (a mistyped
password, an expired/invalid token, a bot probing the login endpoint), so this
event mirrors exactly what triggers Home Assistant's built-in "Login attempt
failed" notification, just with location added.

### About `client` / `client_name`

A normal login carries **no** `client_name`. Home Assistant only sets that for
named long-lived access tokens. The identifying value for a normal login is the
OAuth `client_id`. The `client` field resolves the best label available:
`client_name` if present, else a friendly name for known clients (`iOS app`,
`Android app`, `Web UI` when the id matches your instance URL), else the raw
`client_id`.

## Options

- **Ignore internal system tokens** (default: on): filters out HA's own
  internal tokens, which are used constantly and are not real logins.
- **Add approximate location** (default: off): enrich the event with
  `location`/`city`/`region`/`country` via a keyless lookup (`ipwho.is`).
  **This sends the source IP to a third-party service**, so it is opt-in. The
  lookup runs off the auth path in a background task and never blocks or delays
  authentication; a failed/slow lookup simply fires the event without location.
  Private/LAN/loopback IPs are skipped.

## Install

1. Add this repo to **HACS** as a custom repository (category: Integration) and
   install it. (Or manually copy `custom_components/login_monitor/` into your HA
   `config/custom_components/` directory.) A brand icon is bundled and served
   locally, so no `home-assistant/brands` submission is required.
2. Restart Home Assistant (Developer Tools → restart, or `ha core restart`).
3. Settings → Devices & Services → **Add Integration** → "Login Monitor".
4. Click **Configure** on the integration to set system-token filtering and the
   optional geo lookup (see [Options](#options)).
5. Build automations triggered by the `login_monitor_login` and
   `login_monitor_failed_login` events (see `example_automation.yaml`). The
   successful-login example sends a phone push; the failed-login example creates
   an in-HA persistent notification (the sidebar bell panel); swap either for
   the other action to taste. Both link to AbuseIPDB's reputation page for the
   source IP.

## Caveats

- Detection is **real-time** (fires the moment an access token is created for
  a refresh token this integration hasn't seen before).
- Refresh tokens that already existed when the integration first started are
  seeded as "known" at startup, so restarting Home Assistant does not
  re-notify for every already-logged-in session.
- Long-lived access tokens are used directly and are not minted through the
  wrapped method, so their *use* is not reported (their initial creation is).
