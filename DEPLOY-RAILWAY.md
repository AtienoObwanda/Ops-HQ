# Deploy Ops Brain to Railway

Railway runs your bot 24/7 so you don’t need to keep your laptop on. Free tier is enough for this app.

## Dashboard (CS Command Center) — 3 steps

1. **Add these files to your repo** (if not already there):
   - `api.py`
   - `frontend/index.html`
   - `Procfile`
   - `railway.toml`

2. **Add these env vars in Railway** (Settings → Variables):
   | Variable | Example |
   |----------|---------|
   | `ADMIN_EMAIL` | `atieno@credrails.com` |
   | `ADMIN_PASSWORD` | your-secure-password |
   | `COO_EMAIL` | `coo@credrails.com` |
   | `COO_PASSWORD` | their-password |
   | `DASHBOARD_PORT` | `5001` |

3. **Push to GitHub** → Railway redeploys automatically.  
   Dashboard will be live at **https://your-railway-url.railway.app**

---

## 1. Push your code to GitHub

If you haven’t already:

```bash
git add .
git commit -m "Add Railway deploy"
git push origin main
```

## 2. Create a Railway project

1. Go to [railway.app](https://railway.app) and sign in (GitHub is easiest).
2. Click **New Project**.
3. Choose **Deploy from GitHub repo**.
4. Select your **Ops-HQ** (or the repo you use) and the **main** branch.
5. Railway will detect the repo and may ask for a **root directory** — leave blank so it uses the repo root.

## 3. Configure the service

1. Click the new service (your repo name).
2. Go to **Settings**.
3. **Build**: Railway usually auto-detects Python. If not, set:
   - **Build Command:** (leave empty or `pip install -r requirements.txt`)
   - **Start Command:** `python3 bot.py`  
     Or leave start command empty if you’re using the **Procfile** (Railway will run `worker: python3 bot.py`).
4. **Root Directory:** leave blank if the bot code is at the repo root.

## 4. Set environment variables (required)

If you skip this, the app will crash with `KeyError: 'SLACK_BOT_TOKEN'` or "Missing required env vars".

In the same service: **Variables** (or **Settings** → **Variables**).

Add every variable from your local `.env` (copy the **values**, not the placeholders):

| Variable | Example | Required |
|----------|---------|----------|
| `SLACK_BOT_TOKEN` | `xoxb-...` | Yes |
| `SLACK_SIGNING_SECRET` | ... | Yes |
| `SLACK_APP_TOKEN` | `xapp-...` | Yes |
| `CS_BRIEF_SLACK_USER_ID` | `U0123ABC` | Yes (for DMs) |
| `CS_COMMAND_CHANNEL` | `C0123ABC` | Optional (if using channel instead of DM) |
| `RECON_SLACK_IDS` | `U123,U456` | Optional |
| `CS_BOT_DB` | `cs_bot.db` | Optional (default) |

- Do **not** commit your real `.env` file.
- Paste each value in the Railway UI; Railway will keep them secret.

## 5. Deploy

1. Trigger a deploy: **Deploy** tab → **Deploy** or push a new commit to `main`.
2. Check **Logs** for:
   - `✅ DB initialised at cs_bot.db`
   - `⏰ Scheduler started — ...`
   - `🤖 Ops Brain starting...`
   - `⚡️ Bolt app is running!`

If you see those, the bot is running on Railway.

## 6. Keep it running (free tier)

- Railway may sleep the service on the free tier after inactivity. The bot uses **Socket Mode** (no public URL), so it stays connected; cron jobs (9am brief, 6pm reminder, etc.) will run on Railway’s clock (UTC by default).
- To align schedules with your timezone, you’d need to change the cron times in `bot.py` (e.g. 9am Nairobi = 6am UTC in winter, 7am UTC in summer) or set `TZ` in Railway variables (e.g. `TZ=Africa/Nairobi`) if your stack respects it.

## Troubleshooting

- **Bot doesn’t respond in Slack**  
  Check Railway logs for errors. Confirm all three Slack tokens are set and the app is installed in your workspace.
- **“Application failed to respond”**  
  This app is a worker (no HTTP server). In Railway, make sure the service is set to run the **worker** process (Procfile) or start command `python3 bot.py`, not a web server.
- **Scheduler not firing**  
  Ensure the process is running (logs show “Scheduler started”). Railway runs in UTC; adjust cron hours in `bot.py` if your 9am should be local time.
