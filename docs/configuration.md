# Server Configuration

This document covers Pi-side setup. Device/firmware configuration (board secrets, building, flashing, serial monitor) lives in the firmware repo: [home-hub-firmware/docs/configuration.md](https://github.com/vcchstrandberg/home-hub-firmware/blob/main/docs/configuration.md).

---

## File structure

```
netatmo-home-hub/
├── server/
│   ├── netatmo_proxy.py
│   ├── requirements.txt
│   ├── config.example.env           ← copy to .env and fill in
│   ├── netatmo-proxy.service
│   ├── setup.sh
│   └── update.sh                    ← auto-deploy cron script
└── docs/
```

`.env` is gitignored — credentials never reach GitHub.

---

## Pi credentials (.env)

The Pi reads credentials from `server/.env`. Set them up once — the proxy keeps the refresh token current from then on.

```bash
cp server/config.example.env server/.env
nano server/.env
```

```ini
NETATMO_CLIENT_ID=your-client-id
NETATMO_CLIENT_SECRET=your-client-secret
NETATMO_REFRESH_TOKEN=your-initial-refresh-token
PORT=8080
```

**Where to get them:**

- `CLIENT_ID` and `CLIENT_SECRET` — create one app at [dev.netatmo.com/apps](https://dev.netatmo.com/apps/). Give it any name. These values are the same regardless of how many devices you run.
- `REFRESH_TOKEN` — use the **Token generator** on your app page in the developer portal. Paste this initial token once; the proxy rotates it automatically after that.

> The proxy writes the updated refresh token back to `.env` on every successful OAuth cycle. You never need to touch the token again unless the service is offline for more than 60 days, after which Netatmo expires inactive tokens.

---

## Next steps

- Pi install and systemd service → [raspberry-pi-setup.md](raspberry-pi-setup.md)
- Routes and behavior reference → [server.md](server.md)
- Device firmware configuration → [home-hub-firmware/docs/configuration.md](https://github.com/vcchstrandberg/home-hub-firmware/blob/main/docs/configuration.md)
