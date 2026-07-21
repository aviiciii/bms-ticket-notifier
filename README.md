# BMS Ticket Notifier

Automatically monitors [BookMyShow](https://in.bookmyshow.com) for ticket availability and sends you an email alert when something changes.

Runs every n minutes via GitHub Actions. (You can set it via cron jobs, tho GH Actions doesn't kick in accurately)

## How It Works

1. Fetches showtimes from the BookMyShow API for a given movie URL
2. Compares results with the previous check (stored in `bms_state.json`)
3. Sends an HTML email via [Resend](https://resend.com) if anything changed (new shows, dates opening, availability updates)

## Setup

### 1. Fork this repository

### 2. Set GitHub Secrets

Go to **Settings → Secrets and variables → Actions** and add:

| Secret | Description |
|--------|-------------|
| `RESEND_API_KEY` | API key from [resend.com](https://resend.com) |
| `RESEND_FROM_EMAIL` | Email address to send notifications (use your own domain email or anything@resend.dev) |
| `RESEND_TO_EMAIL` | Email address to receive notifications |
| `NTFY_TOPIC` | (Optional) [ntfy.sh](https://ntfy.sh) topic name for phone push notifications on new showtimes. Pick any hard-to-guess string, e.g. `bms-alerts-x7q2f` |

### 3. Set GitHub Variables

Go to **Settings → Secrets and variables → Actions → Variables** and add:

| Variable | Description | Example |
|----------|-------------|---------|
| `BMS_URL` | BookMyShow ticket page URL | `https://in.bookmyshow.com/movies/chennai/.../ET00123456` |
| `BMS_DATES` | Dates to monitor (YYYYMMDD, comma-separated). Leave empty to auto-detect from URL. | `20260318,20260319` |
| `BMS_THEATRE` | Filter by theatre name (substring match, comma-separated) | `PVR,IMAX` |
| `BMS_TIME` | Filter by time period (comma-separated) | `evening,night` |

**Time periods:** `morning` (6–12), `afternoon` (12–16), `evening` (16–19), `night` (19–24)

### 4. Trigger the workflow

Go to **Actions → BMS Ticket Checker** and click **Run workflow**, or wait for it to run automatically every 30 minutes.

## Local Usage

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --frozen

export BMS_URL="https://in.bookmyshow.com/movies/chennai/.../ET00123456"
export BMS_DATES="20260318,20260319"
export BMS_THEATRE="PVR"
export BMS_TIME="evening,night"
export RESEND_API_KEY="re_..."
export RESEND_TO_EMAIL="you@example.com"
export RESEND_TO_EMAIL="python@resend.dev"

uv run main.py
```

## Notifications

You'll receive an email when:
- A new showtime is added
- A date opens for booking
- Seat availability changes (e.g. sold out → available)

Emails show a summary of what changed and the current status of all monitored shows, grouped by theatre.

### Phone push notifications (optional)

If `NTFY_TOPIC` is set, you'll also get a push notification on your phone via [ntfy.sh](https://ntfy.sh) — but only when a **brand-new showtime** is detected (not for date-opened or availability-changed events, which are email-only).

1. Pick a topic name (treat it like a password — anyone who knows it can read your notifications), e.g. `bms-alerts-x7q2f`
2. Install the [ntfy app](https://ntfy.sh/#subscribe) (iOS/Android) or open `https://ntfy.sh/<your-topic>` in a browser, and subscribe to your topic
3. Add `NTFY_TOPIC` as a GitHub secret with that value

No account or signup needed. If you're self-hosting an ntfy server instead, also set `NTFY_SERVER` (defaults to `https://ntfy.sh`).
