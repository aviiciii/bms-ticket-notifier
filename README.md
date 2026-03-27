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

### 3. Set GitHub Variables

Go to **Settings → Secrets and variables → Actions → Variables** and add:

| Variable | Description | Example |
|----------|-------------|---------|
| `BMS_URL` | BookMyShow ticket page URL | `https://in.bookmyshow.com/movies/chennai/movie-name/ET00123456` |
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

export BMS_URL="https://in.bookmyshow.com/movies/chennai/movie-name/ET00123456"
export BMS_DATES="20260318,20260319"
export BMS_THEATRE="PVR"
export BMS_TIME="evening,night"
export RESEND_API_KEY="re_..."
export RESEND_TO_EMAIL="you@example.com"
export RESEND_FROM_EMAIL="python@resend.dev"

uv run main.py
```

## Notifications

You'll receive an email when:
- A new showtime is added
- A date opens for booking
- Seat availability changes (e.g. sold out → available)

Emails show a summary of what changed and the current status of all monitored shows, grouped by theatre.
