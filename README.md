# Login Monitor (Home Assistant custom integration)

Home Assistant logs **failed** logins but is silent on **successful** ones: no
log line, no event, no notification. This integration closes that gap.

It watches the refresh-token table using only public, supported APIs
(`hass.auth.async_get_users()` → each user's `refresh_tokens`) and fires a bus
event whenever a token's `last_used_at` advances, i.e. whenever an
authenticated session or app successfully accesses your instance.

No monkeypatching of core, no reading `.storage/auth` directly, no removed APIs.

## The event

Event type: **`login_monitor_login`**

| Field | Example | Notes |
|-------|---------|-------|
| `user_name` | `Diego` | HA user the token belongs to |
| `user_id` | `abcd...` | |
| `client_name` | `Home Assistant iOS` / `null` | app / client that authenticated |
| `token_type` | `normal` / `long_lived_access_token` | |
| `ip_address` | `73.12.x.x` | source IP of the access |
| `is_new_ip` | `true` | first time this IP has been seen |
| `last_used_at` | `2026-07-15T14:03:11+00:00` | ISO timestamp (UTC) |

## Options

- **Only fire for new / unrecognized IPs** (default: on) — keeps your own
  phone/browser from notifying you constantly. Turn off to get an event on
  every authenticated access.
- **Ignore internal system tokens** (default: on) — filters out HA's own
  internal tokens, which are used constantly and are not real logins.
- **Poll interval** (default: 20s) — how often the token table is scanned.

## Install

1. Copy `custom_components/login_monitor/` into your HA `config/custom_components/`
   directory (or add this repo to HACS as a custom repository → install).
2. Restart Home Assistant (Developer Tools → restart, or `ha core restart`).
3. Settings → Devices & Services → **Add Integration** → "Login Monitor".
4. Build an automation triggered by the `login_monitor_login` event (see
   `example_automation.yaml`).

## Caveats

- Detection is **poll-based**, so a login is reported within one poll interval.
- It reports *token usage*, which is the practical signal for "someone is
  accessing my instance from this IP". The very first authenticated request
  from a brand-new session creates a new token and fires immediately.
