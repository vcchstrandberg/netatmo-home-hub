# Raspberry Pi Setup

Headless setup — no monitor, keyboard or mouse required. SSH only.

Any Pi with WiFi works: **Pi 3B+, Pi 4, Pi 5, or Zero 2 W** (the Zero 2 W is cheap and draws very little power — ideal for an always-on hub).

---

## 1. Flash the SD card

1. Download and install **[Raspberry Pi Imager](https://www.raspberrypi.com/software/)** on your Mac.
2. Open Imager and click **Choose Device** → select your Pi model.
3. Click **Choose OS** → **Raspberry Pi OS (other)** → **Raspberry Pi OS Lite (64-bit)**.  
   *(Lite = no desktop, SSH only — exactly what we want.)*
4. Click **Choose Storage** → select your SD card.
5. Click **Next**, then when prompted: **Edit Settings**.

In the **OS Customisation** dialog:

| Setting | Value |
|---|---|
| Hostname | `netatmo-hub` |
| Username | `pi` (or your choice) |
| Password | pick a strong password |
| WiFi SSID | your home network name |
| WiFi password | your home network password |
| WiFi country | your country code (e.g. `SE`, `US`, `GB`) |
| Locale / timezone | set to match your location |

6. Switch to the **Services** tab → enable **SSH** → **Use password authentication**.
7. Click **Save** → **Yes** → confirm the write. This takes 1–2 minutes.

---

## 2. First boot

1. Insert the SD card into the Pi and power it on.
2. Wait about 60–90 seconds for first boot to complete.
3. On your Mac, find the Pi's IP address:
   ```bash
   ping netatmo-hub.local
   ```
   If mDNS is slow, check your router's DHCP table instead.
4. SSH in:
   ```bash
   ssh pi@netatmo-hub.local
   ```
5. Update the system (takes a few minutes):
   ```bash
   sudo apt-get update && sudo apt-get upgrade -y
   ```

---

## 3. Clone the repo

```bash
sudo apt-get install -y git
git clone https://github.com/vcchstrandberg/netatmo-home-hub.git
cd netatmo-home-hub/server
```

---

## 4. Configure credentials

Copy the example config and fill in your Netatmo credentials:

```bash
cp config.example.env .env
nano .env
```

Your `.env` should look like:

```
NETATMO_CLIENT_ID=your-client-id
NETATMO_CLIENT_SECRET=your-client-secret
NETATMO_REFRESH_TOKEN=your-refresh-token
PORT=8080
```

**Where to find your credentials:**

- `CLIENT_ID` and `CLIENT_SECRET` — from the [Netatmo developer portal](https://dev.netatmo.com/apps/). Create one app, give it any name.
- `REFRESH_TOKEN` — the easiest source is `arduino_secrets.h` from an existing device in the `netatmo-weather-api` repo. If you don't have one, obtain it via the Netatmo OAuth2 flow in the developer portal (use the "Token generator" on your app page).

> The proxy automatically writes the updated refresh token back to `.env` each time it refreshes. You only need to paste the initial token once.

> **Security:** `.env` is listed in `.gitignore` and will never be committed to the repo.

---

## 5. Install and start the service

```bash
chmod +x setup.sh
./setup.sh
```

Then start and verify:

```bash
sudo systemctl start netatmo-proxy
sudo systemctl status netatmo-proxy
```

You should see the service as **active (running)** and log output like:

```
Initial fetch OK — city: Stockholm
Listening on 0.0.0.0:8080
```

Watch live logs at any time:

```bash
sudo journalctl -u netatmo-proxy -f
```

---

## 6. Verify from your Mac

```bash
curl http://netatmo-hub.local:8080/weather
```

Expected response:

```json
{
  "city": "Stockholm",
  "indoor_temp": 21.5,
  "indoor_humidity": 45,
  "pressure": 1013.2,
  "outdoor_temp": 8.3,
  "rain_1h": 0.0,
  "rain_24h": 2.5,
  "is_raining": false,
  "updated_at": 1747123456
}
```

---

## 7. Note the Pi's IP address

Find the static IP or note the current one:

```bash
ip addr show wlan0 | grep "inet "
```

Put this IP in `PROXY_HOST` in your device `arduino_secrets.h`.

> **Tip:** Assign the Pi a static IP in your router's DHCP settings so the address never changes.

---

## Useful commands

```bash
sudo systemctl restart netatmo-proxy       # restart after config changes
sudo systemctl stop netatmo-proxy          # stop
sudo journalctl -u netatmo-proxy -f        # live log
curl http://netatmo-hub.local:8080/weather # full weather response
curl http://netatmo-hub.local:8080/health  # quick health check
```

---

## Give the Pi a static IP

Assign a reserved IP in your router's DHCP settings (look for "DHCP reservation" or "static lease") using the Pi's MAC address. This ensures `PROXY_HOST` in your device `arduino_secrets.h` never needs updating.

To find the Pi's MAC address:

```bash
ip link show wlan0
```

Look for the `link/ether` line, e.g. `b8:27:eb:xx:xx:xx`.

---

## Troubleshooting

**Service fails to start**
```bash
sudo journalctl -u netatmo-proxy -n 50
```
Most common cause: wrong credentials in `.env`. Double-check `NETATMO_CLIENT_ID`, `NETATMO_CLIENT_SECRET`, and `NETATMO_REFRESH_TOKEN`.

**Token refresh fails (`401 Unauthorized`)**  
The refresh token has expired (this happens if no client has refreshed it for a long time). Generate a new one from the [Netatmo developer portal](https://dev.netatmo.com/apps/) and paste it into `.env`, then restart the service.

**Device can't reach the proxy**  
- Verify the Pi is on: `ping netatmo-hub.local`
- Verify the service is running: `curl http://netatmo-hub.local:8080/health`
- Check `PROXY_HOST` in the device's `arduino_secrets.h` matches the Pi's actual IP

**mDNS (`netatmo-hub.local`) not resolving**  
Use the Pi's IP address directly instead. Find it with `ip addr show wlan0 | grep "inet "` on the Pi.
