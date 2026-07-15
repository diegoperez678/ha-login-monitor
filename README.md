# Login Monitor (Home Assistant custom integration)

Home Assistant logs **failed** logins but is silent on **successful** ones: no
log line, no event, no notification. This integration closes that gap.

It wraps `AuthManager.async_create_access_token` — the single point where every
authenticated session stamps its source IP — and fires a bus event **in real
time** whenever an access token is minted: on login, on a new-device session,
and on token refreshes.

**Safety:** the wrapper forwards its arguments unchanged and returns the
original result *first*; the event fire is fully guarded in `try/except`. A bug
in this integration therefore cannot break authentication — the worst case is a
missed notification while logins keep working. If a future Home Assistant
release renames that method, this integration simply fails to load (auth
untouched).

No reading `.storage/auth`, no removed APIs, no polling.

## Requirements

Home Assistant 2024.1 or newer. The bundled brand icon is served locally and
only appears on HA 2026.3+ (older versions work fine, just without the icon).

## The event

Event type: **`login_monitor_login`**

| Field | Example | Notes |
|-------|---------|-------|
| `user_name` | `Diego` | HA user the token belongs to |
| `user_id` | `abcd...` | |
| `client` | `iOS app` / `Web UI` / `https://...` | friendly label for the client (see below) |
| `client_id` | `https://home-assistant.io/iOS` | raw OAuth client id |
| `client_name` | `null` (usually) | only set for named long-lived tokens |
| `token_type` | `normal` / `long_lived_access_token` | |
| `ip_address` | `73.12.x.x` | source IP of the access |
| `is_new_ip` | `true` | first time this IP has been seen |
| `location` | `Denver, Colorado, United States` | approximate location, only when geo lookup is enabled |
| `city` / `region` / `country` | `Denver` / `Colorado` / `United States` | individual geo fields (geo lookup only) |

### About `client` / `client_name`

A normal login carries **no** `client_name` — Home Assistant only sets that for
named long-lived access tokens. The identifying value for a normal login is the
OAuth `client_id`. The `client` field resolves the best label available:
`client_name` if present, else a friendly name for known clients (`iOS app`,
`Android app`, `Web UI` when the id matches your instance URL), else the raw
`client_id`.

## Options

- **Only fire for new / unrecognized IPs** (default: on) — keeps your own
  phone/browser (and routine ~30-min token refreshes) from notifying you
  constantly. Turn off to get an event on every access-token creation.
- **Ignore internal system tokens** (default: on) — filters out HA's own
  internal tokens, which are used constantly and are not real logins.
- **Add approximate location** (default: off) — enrich the event with
  `location`/`city`/`region`/`country` via a keyless lookup (`ipwho.is`).
  **This sends the source IP to a third-party service**, so it is opt-in. The
  lookup runs off the auth path in a background task and never blocks or delays
  authentication; a failed/slow lookup simply fires the event without location.
  Private/LAN/loopback IPs are skipped.

## Install

1. Add this repo to **HACS** as a custom repository (category: Integration) and
   install it. (Or manually copy `custom_components/login_monitor/` into your HA
   `config/custom_components/` directory.) A brand icon is bundled and served
   locally — no `home-assistant/brands` submission is required.
2. Restart Home Assistant (Developer Tools → restart, or `ha core restart`).
3. Settings → Devices & Services → **Add Integration** → "Login Monitor".
4. Click **Configure** on the integration to set new-IP-only, system-token
   filtering, and the optional geo lookup (see [Options](#options)).
5. Build an automation triggered by the `login_monitor_login` event (see
   `example_automation.yaml`). The example notifies your phone and, on tap,
   opens AbuseIPDB's reputation page for the source IP.

## Caveats

- Detection is **real-time** (fires the moment an access token is created).
- Fires on token *refreshes* as well as fresh logins; leave **new-IP-only** on
  (the default) so routine same-IP refreshes stay silent.
- Long-lived access tokens are used directly and are not minted through the
  wrapped method, so their *use* is not reported (their initial creation is).
