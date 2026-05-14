# Server Reference

The proxy is a single Python script (`server/netatmo_proxy.py`) running under Flask. It starts a background polling thread and serves four HTTP routes.

---

## Routes

### `GET /weather`

Returns the cached weather data as a flat JSON object. This is what all display devices call.

```json
{
  "city":            "Stockholm",
  "indoor_temp":     21.5,
  "indoor_humidity": 45,
  "pressure":        1013.2,
  "outdoor_temp":    8.3,
  "rain_1h":         0.0,
  "rain_24h":        2.5,
  "is_raining":      false,
  "updated_at":      1747123456
}
```

| Field | Type | Source |
|---|---|---|
| `city` | string | `devices[0].place.city` |
| `indoor_temp` | float °C | Base station `dashboard_data.Temperature` |
| `indoor_humidity` | int % | Base station `dashboard_data.Humidity` |
| `pressure` | float hPa | Base station `dashboard_data.Pressure` |
| `outdoor_temp` | float °C | NAModule1 `dashboard_data.Temperature` |
| `rain_1h` | float mm | NAModule3 `dashboard_data.sum_rain_1` |
| `rain_24h` | float mm | NAModule3 `dashboard_data.sum_rain_24` |
| `is_raining` | bool | NAModule3 `dashboard_data.Rain > 0` |
| `updated_at` | int | Unix timestamp of last successful poll |

Returns **503** if no data has been fetched yet (e.g., during startup or after a failed initial fetch).

---

### `GET /health`

Lightweight health check — useful for monitoring scripts or router uptime checks.

```json
{"ok": true, "has_data": true}
```

`has_data` is `false` until the first successful Netatmo poll.

---

### `GET /log`

Returns the rolling log buffer as plain text, one entry per line. Used by the web UI's JavaScript to fetch updates without reloading the page.

```
[18:08:14] Token refreshed, expires in 10800s
[18:08:14] Updated — Stockholm  in=21.5°  out=8.3°
[18:08:14] Listening on 0.0.0.0:8080
[18:08:25] HTTP GET / → 200
[18:08:46] HTTP GET /weather → 200
```

Response includes `Cache-Control: no-cache, no-store, must-revalidate` to prevent browsers from caching stale log content.

---

### `GET /`

Web UI — a dark-themed HTML page showing current weather data and the live log.

The weather table is rendered server-side on each page load. The log section is a `<div>` updated every 10 seconds by JavaScript that fetches `/log?t=<timestamp>` (the `?t=` parameter busts any remaining cache). The page subtitle shows the time of the last successful log fetch.

Access at: `http://netatmo-hub.local:8080/`

---

## Log buffer

All application events go through `_log(msg)`, which both prints to stdout (captured by journald) and appends to `_log_buffer` (a `collections.deque` with `maxlen=500`). HTTP requests are also logged via an `@app.after_request` hook, excluding `/log` itself to avoid noise from JS polling.

Events that appear in the log:

| Event | Example |
|---|---|
| Service start | `[18:08:13] Netatmo proxy starting...` |
| Token refresh | `[18:08:14] Token refreshed, expires in 10800s` |
| Weather update | `[18:08:14] Updated — Stockholm  in=21.5°  out=8.3°` |
| Flask started | `[18:08:14] Listening on 0.0.0.0:8080` |
| HTTP request | `[18:08:25] HTTP GET /weather → 200` |
| Fetch error | `[18:13:14] Fetch error: ConnectionError(...)` |

---

## Token refresh

The proxy calls the Netatmo OAuth2 token endpoint before every weather fetch, whenever the cached token is within 60 seconds of expiry. On first fetch the token is always expired, so a refresh always happens at startup.

```
POST https://api.netatmo.com/oauth2/token
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token
&client_id=<CLIENT_ID>
&client_secret=<CLIENT_SECRET>
&refresh_token=<NETATMO_REFRESH_TOKEN>
```

The new `refresh_token` is immediately written back to `.env` via `python-dotenv`'s `set_key()`. This ensures the latest token survives a Pi reboot.

Refresh tokens expire after **60 days of inactivity**. If the Pi is offline for that long, paste a new token into `.env` and restart the service:

```bash
sudo nano /home/pi/netatmo-home-hub/server/.env
sudo systemctl restart netatmo-proxy
```

---

## Systemd service

The service is installed by `setup.sh` and managed with `systemctl`:

```bash
sudo systemctl start   netatmo-proxy    # start
sudo systemctl stop    netatmo-proxy    # stop
sudo systemctl restart netatmo-proxy    # restart after config changes
sudo systemctl status  netatmo-proxy    # show current status
sudo systemctl enable  netatmo-proxy    # auto-start on boot (done by setup.sh)
sudo journalctl -u netatmo-proxy -f     # follow live logs
sudo journalctl -u netatmo-proxy -n 50  # last 50 lines
```

The service is configured with `Restart=on-failure` and `RestartSec=15` — it automatically recovers from crashes or transient network errors.

---

## Updating the Pi

To deploy a new version of the proxy:

```bash
ssh pi@netatmo-hub.local
cd netatmo-home-hub
git pull
sudo systemctl restart netatmo-proxy
```

All connected devices continue to work during the restart — they simply get a 503 on any request that lands in the brief gap while Flask is starting up, then recover on the next poll.
