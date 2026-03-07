# Ops Brain — Setup Guide
# Read this top to bottom. Takes ~20 minutes.

# ═══════════════════════════════════════════════════════
# STEP 1: CREATE YOUR SLACK APP
# ═══════════════════════════════════════════════════════

# 1. Go to https://api.slack.com/apps
# 2. Click "Create New App" → "From scratch"
# 3. Name it: "Ops Brain" — pick your workspace
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
#   /askrecon   → Ask recon/QAs for status on Internal User Testing projects
#   /brief      → Get your morning brief now
#   /jira       → Test Jira connection (configured? stale/blocked/changes count)
#   /report     → COO-ready summary
#   /braindump  → Save evening brain dump (Mon–Fri; used in week/month reports)
#   /weekreport → Week in review from brain dumps
#   /monthreport → Month in review from brain dumps
#   /issue      → Log a client issue
#   /issues     → View open issues
#   /help       → Show all commands

# ── Interactivity ─────────────────────────────────────
# Left sidebar → "Interactivity & Shortcuts" → Turn ON
# Request URL: https://placeholder.com (Socket Mode handles it)

# ── Reinstall App ─────────────────────────────────────
# After adding all permissions:
# Left sidebar → "Install App" → Reinstall to Workspace

# If a slash command shows "dispatch_failed":
# - Make sure that command is listed above and created under Slash Commands (exact name, e.g. askrecon).
# - After adding a new command, reinstall the app to the workspace (Install App → Reinstall).
# - Ensure the bot is running (e.g. python3 bot.py) and Socket Mode is enabled.


# ═══════════════════════════════════════════════════════
# STEP 2: WHERE THE MORNING BRIEF GOES
# ═══════════════════════════════════════════════════════

# Option A — To your DMs (easiest):
# 1. In Slack, click your profile (top right) → "Profile" → three dots → "Copy member ID"
# 2. Paste into .env as CS_BRIEF_SLACK_USER_ID=U0123ABC (your ID starts with U)

# Option B — To a channel:
# 1. Create a private channel (e.g. #ops-brain) and add Ops Brain to it
# 2. Right-click the channel → "Copy Link" — the part after the last / (starts with C...) is the channel ID
# 3. Paste into .env as CS_COMMAND_CHANNEL=C0123ABC


# ═══════════════════════════════════════════════════════
# STEP 3: INSTALL AND RUN
# ═══════════════════════════════════════════════════════

# Make sure you have Python 3.9+
python3 --version

# Install dependencies (use pip3 if pip is not found)
pip3 install -r requirements.txt
# or: python3 -m pip install -r requirements.txt

# Set up your environment
cp .env.example .env
# Now edit .env with your actual tokens

# Load env and run
export $(cat .env | grep -v '#' | xargs) && python3 bot.py

# You should see:
# ✅ DB initialised at cs_bot.db
# ⏰ Scheduler started — briefs at 9am, check-ins at 4pm
# 🤖 Ops Brain starting...
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

# Ops Brain will:
# - Show their name in the brief
# - DM them at 4pm for check-ins
# - Their button clicks auto-update the project health


# ═══════════════════════════════════════════════════════
# KEEP IT RUNNING (optional)
# ═══════════════════════════════════════════════════════

# Option A: Run in background on your machine
nohup python3 bot.py > bot.log 2>&1 &

# Option B: Use screen
screen -S opsbrain
python3 bot.py
# Ctrl+A then D to detach

# Option C: Deploy to Railway.app (free tier, always-on)
# Push this folder to GitHub → connect to Railway → set env vars in dashboard
# That's it. Free forever for this scale.


# ═══════════════════════════════════════════════════════
# WHAT HAPPENS AUTOMATICALLY
# ═══════════════════════════════════════════════════════

# 9:00 AM daily → Morning brief posted to your DMs (or your channel if you use one)
#   - Yesterday’s brain dump (if you used /braindump)
#   - Stale projects (no update in 24h)
#   - At risk / blocked projects
#   - Full pipeline snapshot

# 6:00 PM Mon–Fri → You get a DM reminder to do an evening brain dump (/braindump <notes>)
# Saturday 9:00 AM → Week in Review (Mon–Fri brain dumps) posted to your DMs
# 1st of month 9:00 AM → Month in Review (previous month’s brain dumps) posted to your DMs
# 10:00 AM Mon–Fri → Recon/QAs get a DM with projects in Internal User Testing; asked for status (set RECON_SLACK_IDS in .env)
# 4:00 PM daily → Engineers get DMs
#   - One message per engineer listing their projects
#   - 3 buttons: On Track / At Risk / Blocked
#   - Their click = update logged, you never chase again
