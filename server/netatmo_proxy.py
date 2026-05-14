#!/usr/bin/env python3
"""
Netatmo local proxy — runs on a Raspberry Pi.

Refreshes Netatmo tokens automatically, polls the API every 5 minutes,
and serves the latest weather data as a flat JSON on GET /weather.
Devices on the local network call this instead of Netatmo directly.
"""
import os
import time
import threading

import requests
from dotenv import load_dotenv, set_key
from flask import Flask, jsonify, abort

load_dotenv()

CLIENT_ID     = os.environ["NETATMO_CLIENT_ID"]
CLIENT_SECRET = os.environ["NETATMO_CLIENT_SECRET"]

TOKEN_URL = "https://api.netatmo.com/oauth2/token"
DATA_URL  = "https://api.netatmo.com/api/getstationsdata"
POLL_SECS = 300   # match Netatmo station update interval
ENV_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

_lock          = threading.Lock()
_access_token  = None
_refresh_token = os.environ["NETATMO_REFRESH_TOKEN"]
_token_expiry  = 0.0
_weather       = None

app = Flask(__name__)


def _refresh_token_fn():
    global _access_token, _refresh_token, _token_expiry
    r = requests.post(TOKEN_URL, data={
        "grant_type":    "refresh_token",
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": _refresh_token,
    }, timeout=10)
    r.raise_for_status()
    data = r.json()
    _access_token  = data["access_token"]
    _refresh_token = data["refresh_token"]
    _token_expiry  = time.time() + data["expires_in"] - 60
    set_key(ENV_FILE, "NETATMO_REFRESH_TOKEN", _refresh_token)
    print(f"[{_ts()}] Token refreshed, expires in {data['expires_in']}s", flush=True)


def _fetch():
    global _weather
    if time.time() >= _token_expiry:
        _refresh_token_fn()

    r = requests.get(DATA_URL,
                     headers={"Authorization": f"Bearer {_access_token}"},
                     timeout=10)
    r.raise_for_status()
    raw = r.json()

    device  = raw["body"]["devices"][0]
    city    = device.get("place", {}).get("city", "")
    indoor  = device.get("dashboard_data", {})
    outdoor, rain = {}, {}
    for mod in device.get("modules", []):
        t = mod.get("type")
        if t == "NAModule1":
            outdoor = mod.get("dashboard_data", {})
        elif t == "NAModule3":
            rain = mod.get("dashboard_data", {})

    with _lock:
        _weather = {
            "city":            city,
            "indoor_temp":     indoor.get("Temperature", 0),
            "indoor_humidity": indoor.get("Humidity", 0),
            "pressure":        indoor.get("Pressure", 0),
            "outdoor_temp":    outdoor.get("Temperature", 0),
            "rain_1h":         rain.get("sum_rain_1", 0),
            "rain_24h":        rain.get("sum_rain_24", 0),
            "is_raining":      rain.get("Rain", 0) > 0,
            "updated_at":      int(time.time()),
        }
    print(f"[{_ts()}] Updated — {city} in={indoor.get('Temperature')}°  out={outdoor.get('Temperature')}°", flush=True)


def _poll_loop():
    while True:
        time.sleep(POLL_SECS)
        try:
            _fetch()
        except Exception as e:
            print(f"[{_ts()}] Fetch error: {e}", flush=True)


def _ts():
    return time.strftime("%H:%M:%S")


@app.route("/weather")
def weather():
    with _lock:
        if _weather is None:
            abort(503)
        return jsonify(_weather)


@app.route("/health")
def health():
    with _lock:
        return jsonify({"ok": True, "has_data": _weather is not None})


if __name__ == "__main__":
    print("Netatmo proxy starting...", flush=True)
    try:
        _fetch()
    except Exception as e:
        print(f"Initial fetch failed: {e}", flush=True)

    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()

    port = int(os.environ.get("PORT", 8080))
    print(f"Listening on 0.0.0.0:{port}", flush=True)
    app.run(host="0.0.0.0", port=port)
