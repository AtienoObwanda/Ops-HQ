# CS Bot — Setup Guide
# Read this top to bottom. Takes ~20 minutes.

# ═══════════════════════════════════════════════════════
# STEP 1: CREATE YOUR SLACK APP
# ═══════════════════════════════════════════════════════

# 1. Go to https://api.slack.com/apps
# 2. Click "Create New App" → "From scratch"
# 3. Name it: "CS Bot" — pick your Credrails workspace
# 4. Click Create

# ── OAuth & Permissions ───────────────────────────────
# Left sidebar → "OAuth & Permissions"
# Under "Bot Token Scopes", add ALL of these:

#   chat:write          (send messages)
#   chat:write.public   (post to channels)
#   commands            (slash commands)
#   im:write            (DM engineers)
#   users:read          (resolve user names)
#   channels:read       (read channel info)

# Click "Install to Workspace" → copy the Bot User OAuth Token
# Starts with: xoxb-...
# → Paste into .env as SLACK_BOT_TOKEN

# ── Socket Mode ───────────────────────────────────────
# Left sidebar → "Socket Mode" → Enable it
# Generate an App-Level Token with scope: connections:write
# Starts with: xapp-...
# → Paste into .env as SLACK_APP_TOKEN

# ── Signing Secret ────────────────────────────────────
# Left sidebar → "Basic Information" → "App Credentials"
# Copy "Signing Secret"
# → Paste into .env as SLACK_SIGNING_SECRET

# ── Slash Commands ────────────────────────────────────
# Left sidebar → "Slash Commands" → Create New Command
# Add ALL of these (Request URL can be anything for Socket Mode, use https://placeholder.com):

#   /project    → Manage projects
#   /update     → Log a project update
#   /stage      → Move project to new stage
#   /risk       → Flag a project at risk
#   /resolve    → Mark project back on track
#   /brief      → Get your morning brief now
#   /report     → COO-ready summary
#   /issue      → Log a client issue
#   /issues     → View open issues
#   /help       → Show all commands

# ── Interactivity ─────────────────────────────────────
# Left sidebar → "Interactivity & Shortcuts" → Turn ON
# Request URL: https://placeholder.com (Socket Mode handles it)

# ── Reinstall App ─────────────────────────────────────
# After adding all permissions:
# Left sidebar → "Install App" → Reinstall to Workspace


# ═══════════════════════════════════════════════════════
# STEP 2: CREATE YOUR PRIVATE CHANNEL
# ═══════════════════════════════════════════════════════

# In Slack:
# 1. Create a private channel: #cs-command
# 2. Add the CS Bot to it (mention @CS Bot or use /invite)
# 3. Right-click the channel → "Copy Link"
#    URL looks like: https://app.slack.com/client/T.../C0123ABC
#    The part starting with C... is your channel ID
# 4. Paste into .env as CS_COMMAND_CHANNEL


# ═══════════════════════════════════════════════════════
# STEP 3: INSTALL AND RUN
# ═══════════════════════════════════════════════════════

# Make sure you have Python 3.9+
python3 --version

# Install dependencies
pip install -r requirements.txt

# Set up your environment
cp .env.example .env
# Now edit .env with your actual tokens

# Load env and run
export $(cat .env | grep -v '#' | xargs) && python bot.py

# You should see:
# ✅ DB initialised at cs_bot.db
# ⏰ Scheduler started — briefs at 9am, check-ins at 4pm
# 🤖 CS Bot starting...
# ⚡️ Bolt app is running!


# ═══════════════════════════════════════════════════════
# STEP 4: TEST IT
# ═══════════════════════════════════════════════════════

# In Slack, try these commands:

/help
# → Should show the full command list

/project add "Equity Bank" "Recon Integration"
# → Adds your first project

/project list
# → Shows all active projects

/stage "Equity Bank" UAT
# → Moves project to UAT stage

/risk "Equity Bank" "API keys not received from client"
# → Flags it at risk

/brief
# → Your on-demand morning brief

/report
# → COO-ready summary


# ═══════════════════════════════════════════════════════
# STEP 5: ADD YOUR ENGINEERS
# ═══════════════════════════════════════════════════════

# When adding a project, mention the engineer to assign them:
/project add "Client X" "Integration Project" @engineer_name

# The bot will:
# - Show their name in the brief
# - DM them at 4pm for check-ins
# - Their button clicks auto-update the project health


# ═══════════════════════════════════════════════════════
# KEEP IT RUNNING (optional)
# ═══════════════════════════════════════════════════════

# Option A: Run in background on your machine
nohup python bot.py > bot.log 2>&1 &

# Option B: Use screen
screen -S csbot
python bot.py
# Ctrl+A then D to detach

# Option C: Deploy to Railway.app (free tier, always-on)
# Push this folder to GitHub → connect to Railway → set env vars in dashboard
# That's it. Free forever for this scale.


# ═══════════════════════════════════════════════════════
# WHAT HAPPENS AUTOMATICALLY
# ═══════════════════════════════════════════════════════

# 9:00 AM daily → Morning brief posted to #cs-command
#   - Stale projects (no update in 24h)
#   - At risk / blocked projects
#   - Full pipeline snapshot

# 4:00 PM daily → Engineers get DMs
#   - One message per engineer listing their projects
#   - 3 buttons: On Track / At Risk / Blocked
#   - Their click = update logged, you never chase again
