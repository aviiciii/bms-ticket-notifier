"""
BMS Ticket Checker — CI/Headless mode for GitHub Actions.
Runs once, checks all configured watches, emails on changes.
State is persisted via a JSON artifact.

Configure via environment variables or edit the CONFIG below.
"""

import os
import re
import sys
import json
from html import escape
from datetime import datetime
from dataclasses import dataclass, field
from urllib.parse import urlparse
import requests

# ──────────────────────────────────────────────────────────────────────
# CONFIGURATION — edit these or set via env vars
# ──────────────────────────────────────────────────────────────────────
CONFIG = {
    "urls": os.getenv(
        "BMS_URLS",
        "https://in.bookmyshow.com/movies/chennai/dhurandhar-the-revenge/buytickets/ET00478890"
    ),  # comma-separated URLs
    "dates": os.getenv("BMS_DATES", ""),          # comma-separated YYYYMMDD, empty = from URL
    "theatre": os.getenv("BMS_THEATRE", ""),       # substring filter, empty = all
    "time_period": os.getenv("BMS_TIME", ""),      # e.g. "evening,night", empty = all
}

# Fallback to BMS_URL for backwards compatibility
if not os.getenv("BMS_URLS") and os.getenv("BMS_URL"):
    CONFIG["urls"] = os.getenv("BMS_URL")

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_TO_EMAIL = os.getenv("RESEND_TO_EMAIL", "")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "aviiciii@resend.dev")

NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")
NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh")

STATE_FILE = "bms_state.json"

# ──────────────────────────────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────────────────────────────
AVAIL_STATUS_MAP = {
    "0": ("SOLD OUT",    "🔴"),
    "1": ("ALMOST FULL", "🟡"),
    "2": ("FILLING FAST","🟠"),
    "3": ("AVAILABLE",   "🟢"),
}

DATE_STYLE_MAP = {
    "date-selected": "BOOKABLE",
    "date-disabled": "NOT_OPEN",
    "date-default":  "AVAILABLE",
}

TIME_PERIODS = {
    "morning":   (600, 1200),
    "afternoon": (1200, 1600),
    "evening":   (1600, 1900),
    "night":     (1900, 2400),
}

REGION_MAP = {
    "chennai":    ("CHEN",   "chennai",    "13.056", "80.206", "tf3"),
    "mumbai":     ("MUMBAI", "mumbai",     "19.076", "72.878", "te7"),
    "delhi-ncr":  ("NCR",    "delhi-ncr",  "28.613", "77.209", "ttn"),
    "delhi":      ("NCR",    "delhi-ncr",  "28.613", "77.209", "ttn"),
    "bengaluru":  ("BANG",   "bengaluru",  "12.972", "77.594", "tdr"),
    "bangalore":  ("BANG",   "bengaluru",  "12.972", "77.594", "tdr"),
    "hyderabad":  ("HYD",    "hyderabad",  "17.385", "78.487", "tep"),
    "kolkata":    ("KOLK",   "kolkata",    "22.573", "88.364", "tun"),
    "pune":       ("PUNE",   "pune",       "18.520", "73.856", "te2"),
    "kochi":      ("KOCH",   "kochi",      "9.932",  "76.267", "t9z"),
}


# ─────────────────────────────────────────────────────────────────────
# DATA
# ──────────────────────────────────────────────────────────────────────
@dataclass
class CatInfo:
    name: str
    price: str
    status: str

@dataclass
class ShowInfo:
    venue_code: str
    venue_name: str
    session_id: str
    date_code: str
    time: str
    time_code: str
    screen_attr: str
    event_code: str  # Track which event this show belongs to
    categories: list[CatInfo] = field(default_factory=list)

@dataclass
class DateInfo:
    date_code: str
    status: str


# ──────────────────────────────────────────────────────────────────────
# URL PARSER + REGION RESOLVER
# ──────────────────────────────────────────────────────────────────────
def parse_bms_url(url):
    path = urlparse(url).path.strip("/")
    parts = path.split("/")
    result = {"event_code": None, "date_code": None, "region_slug": None}
    for p in parts:
        if re.match(r"^ET\d{8,}$", p):
            result["event_code"] = p
        elif re.match(r"^\d{8}$", p):
            result["date_code"] = p
    if "movies" in parts:
        idx = parts.index("movies")
        if idx + 1 < len(parts):
            result["region_slug"] = parts[idx + 1]
    return result


def resolve_region(slug):
    key = (slug or "").lower().strip()
    if key in REGION_MAP:
        return REGION_MAP[key]
    return (key.upper()[:6], key, "0", "0", "")


# ──────────────────────────────────────────────────────────────────────
# BMS API
# ──────────────────────────────────────────────────────────────────────
API_URL = (
    "https://in.bookmyshow.com/api/movies-data/v4/"
    "showtimes-by-event/primary-dynamic"
)


def fetch_bms(event_code, date_code, region_code, region_slug,
              lat, lon, geohash):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/145.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": (
            f"https://in.bookmyshow.com/movies/"
            f"{region_slug}/buytickets/{event_code}/"
        ),
        "sec-ch-ua": '"Chromium";v="145", "Not:A-Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "x-app-code": "WEB",
        "x-region-code": region_code,
        "x-region-slug": region_slug,
        "x-geohash": geohash,
        "x-latitude": lat,
        "x-longitude": lon,
        "x-location-selection": "manual",
        "x-lsid": "",
    }
    params = {
        "eventCode": event_code,
        "dateCode": date_code or "",
        "isDesktop": "true",
        "regionCode": region_code,
        "xLocationShared": "false",
        "memberId": "", "lsId": "", "subCode": "",
        "lat": lat, "lon": lon,
    }
    try:
        resp = requests.get(API_URL, headers=headers,
                            params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        print(f"  HTTP {resp.status_code}")
    except requests.RequestException as e:
        print(f"  Request failed: {e}")
    return None


# ──────────────────────────────────────────────────────────────────────
# PARSERS
# ──────────────────────────────────────────────────────────────────────
def parse_movie_info(data):
    info = {"name": "Unknown Movie", "language": ""}
    for w in data.get("data", {}).get("topStickyWidgets", []):
        if w.get("type") == "horizontal-text-list":
            for item in w.get("data", []):
                for row in item.get("leftText", {}).get("data", []):
                    for c in row.get("components", []):
                        if "•" in c.get("text", ""):
                            info["language"] = c["text"].strip()
    bs = data.get("data", {}).get("bottomSheetData", {})
    for w in bs.get("format-selector", {}).get("widgets", []):
        if w.get("type") == "vertical-text-list":
            for d in w.get("data", []):
                if d.get("styleId") == "bottomsheet-subtitle":
                    info["name"] = d.get("text", info["name"])
    return info


def parse_dates(data):
    dates = []
    for w in data.get("data", {}).get("topStickyWidgets", []):
        if w.get("type") != "horizontal-block-list":
            continue
        for item in w.get("data", []):
            texts = item.get("data", [])
            if len(texts) >= 3:
                style = item.get("styleId", "")
                dates.append(DateInfo(
                    date_code=item.get("id", ""),
                    status=DATE_STYLE_MAP.get(style, "UNKNOWN"),
                ))
    return dates


def parse_shows(data, event_code):
    shows = []
    for w in data.get("data", {}).get("showtimeWidgets", []):
        if w.get("type") != "groupList":
            continue
        for g in w.get("data", []):
            if g.get("type") != "venueGroup":
                continue
            for card in g.get("data", []):
                if card.get("type") != "venue-card":
                    continue
                addl = card.get("additionalData", {})
                vname = addl.get("venueName", "Unknown")
                vcode = addl.get("venueCode", "")

                for st in card.get("showtimes", []):
                    sa = st.get("additionalData", {})
                    date_code = str(
                        sa.get("showDateCode", "")
                        or sa.get("dateCode", "")
                    ).strip()
                    if not date_code and re.match(
                            r"^\d{8}", sa.get("cutOffDateTime", "")):
                        date_code = sa["cutOffDateTime"][:8]

                    show = ShowInfo(
                        venue_code=vcode,
                        venue_name=vname,
                        session_id=sa.get("sessionId", ""),
                        date_code=date_code,
                        time=st.get("title", ""),
                        time_code=sa.get("showTimeCode", ""),
                        screen_attr=(st.get("screenAttr", "")
                                     or sa.get("attributes", "")),
                        event_code=event_code,
                    )
                    for cat in sa.get("categories", []):
                        ca = str(cat.get("availStatus", ""))
                        lbl, _ = AVAIL_STATUS_MAP.get(ca, ("UNKNOWN", ""))
                        show.categories.append(CatInfo(
                            name=cat.get("priceDesc", ""),
                            price=cat.get("curPrice", "0"),
                            status=ca,
                        ))
                    shows.append(show)
    return shows


# ──────────────────────────────────────────────────────────────────────
# FILTERING
# ──────────────────────────────────────────────────────────────────────
def filter_shows(shows, theatre_filter, time_periods, date_codes):
    result = []
    kws = [k.strip().lower() for k in theatre_filter.split(",")
           if k.strip()] if theatre_filter else []
    periods = [p.strip().lower() for p in time_periods.split(",")
               if p.strip()] if time_periods else []
    dates_set = set(d.strip() for d in date_codes.split(",")
                    if d.strip()) if date_codes else set()

    for s in shows:
        # Theatre filter
        if kws:
            name_lower = s.venue_name.lower()
            if not any(k in name_lower for k in kws):
                continue

        # Date filter
        if dates_set and s.date_code and s.date_code not in dates_set:
            continue

        # Time period filter
        if periods:
            try:
                tc = int(s.time_code)
            except ValueError:
                tc = 0
            matched = False
            for p in periods:
                if p in TIME_PERIODS:
                    lo, hi = TIME_PERIODS[p]
                    if lo <= tc < hi:
                        matched = True
                        break
            if not matched:
                continue

        result.append(s)
    return result


# ──────────────────────────────────────────────────────────────────────
# STATE (for change detection between runs)
# ──────────────────────────────────────────────────────────────────────
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def build_state(shows, dates):
    """Build a comparable state dict."""
    show_state = {}
    for s in shows:
        for c in s.categories:
            key = f"{s.event_code}|{s.venue_code}|{s.session_id}|{s.date_code}|{c.name}"
            show_state[key] = {
                "event": s.event_code,
                "venue": s.venue_name,
                "time": s.time,
                "date": s.date_code,
                "cat": c.name,
                "price": c.price,
                "status": c.status,
            }

    date_state = {
        d.date_code: d.status for d in dates
    }

    return {"shows": show_state, "dates": date_state}


def detect_changes(old_state, new_state):
    changes = []

    # New dates opening
    old_dates = old_state.get("dates", {})
    new_dates = new_state.get("dates", {})
    for dc, status in new_dates.items():
        old_status = old_dates.get(dc)
        if (old_status == "NOT_OPEN"
                and status in ("BOOKABLE", "AVAILABLE")):
            changes.append(f"📅 NEW DATE OPENED: {dc}")

    old_shows = old_state.get("shows", {})
    new_shows = new_state.get("shows", {})

    # New showtimes
    for key in set(new_shows) - set(old_shows):
        s = new_shows[key]
        changes.append(
            f"🆕 NEW: {s['venue']} {s['time']} [{s['date']}] "
            f"— {s['cat']} ₹{s['price']}"
        )

    # Sold out → available
    for key, new_s in new_shows.items():
        old_s = old_shows.get(key)
        if old_s and old_s["status"] == "0" and new_s["status"] != "0":
            lbl, ico = AVAIL_STATUS_MAP.get(
                new_s["status"], ("UNKNOWN", "⚪")
            )
            changes.append(
                f"{ico} BACK: {new_s['venue']} {new_s['time']} "
                f"[{new_s['date']}] — {new_s['cat']} → {lbl}"
            )

    return changes


# ──────────────────────────────────────────────────────────────────────
# EMAIL NOTIFICATION (Resend)
# ──────────────────────────────────────────────────────────────────────
def _cat_status_label(status):
    return AVAIL_STATUS_MAP.get(status, ("UNKNOWN", ""))[0]


def _fmt_date(date_code):
    """Convert YYYYMMDD -> 'Wed, 22 Jul' for display. Falls back to raw code."""
    if not date_code or len(date_code) != 8:
        return date_code or "—"
    try:
        d = datetime.strptime(date_code, "%Y%m%d")
        return d.strftime("%a, %d %b")
    except ValueError:
        return date_code


def send_email(subject, changes, shows, movies):
    """
    movies: dict of event_code -> {"name": ..., "language": ...}
    shows: list of ShowInfo, each tagged with .event_code
    """
    api_key = RESEND_API_KEY.strip()
    to = RESEND_TO_EMAIL.strip()
    frm = RESEND_FROM_EMAIL.strip() or "onboarding@resend.dev"

    if not api_key or not to:
        print("  ⚠️  Skipping email — RESEND_API_KEY or RESEND_TO_EMAIL not set.")
        return

    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")
    movie_names = [m.get("name", "Movie") for m in movies.values()] or ["Movie"]
    header_title = movie_names[0] if len(movie_names) == 1 else f"{len(movie_names)} movies"

    # Build changes HTML
    changes_html = ""
    if changes:
        rows = "".join(
            f'<li style="padding:3px 0;font-size:14px;">{escape(c)}</li>'
            for c in changes
        )
        changes_html = f"""
        <h3 style="margin:0 0 8px 0;font-size:15px;font-weight:bold;color:#333;">
            Changes Detected
        </h3>
        <ul style="margin:0 0 20px 0;padding-left:20px;line-height:1.6;color:#333;">
            {rows}
        </ul>"""

    # Build shows section grouped by movie, then venue
    movie_groups = {}
    for s in shows:
        movie_groups.setdefault(s.event_code, []).append(s)

    shows_html = ""
    for event_code, mshows in movie_groups.items():
        mname = movies.get(event_code, {}).get("name", "Unknown Movie")

        venue_groups = {}
        for s in mshows:
            venue_groups.setdefault(s.venue_name, []).append(s)

        venues_html = ""
        for vname, vshows in venue_groups.items():
            vshows_sorted = sorted(vshows, key=lambda s: (s.date_code, s.time_code))
            show_rows = ""
            for s in vshows_sorted:
                cats = " | ".join(
                    f"{escape(c.name)} Rs.{escape(c.price)} ({_cat_status_label(c.status)})"
                    for c in s.categories
                )
                fmt = f" [{escape(s.screen_attr)}]" if s.screen_attr else ""
                show_rows += (
                    f'<tr>'
                    f'<td style="padding:5px 8px;border-bottom:1px solid #ddd;'
                    f'font-size:13px;vertical-align:top;white-space:nowrap;">'
                    f'{escape(_fmt_date(s.date_code))}</td>'
                    f'<td style="padding:5px 8px;border-bottom:1px solid #ddd;'
                    f'font-size:13px;vertical-align:top;">'
                    f'{escape(s.time)}{fmt}</td>'
                    f'<td style="padding:5px 8px;border-bottom:1px solid #ddd;'
                    f'font-size:13px;vertical-align:top;">'
                    f'{cats}</td>'
                    f'</tr>'
                )

            venues_html += f"""
            <p style="margin:14px 0 4px 0;font-size:14px;font-weight:bold;color:#333;">
                {escape(vname)}
            </p>
            <table style="width:100%;border-collapse:collapse;font-size:13px;">
                <tr style="background:#f5f5f5;">
                    <th style="padding:5px 8px;text-align:left;border-bottom:1px solid #ddd;
                               font-weight:bold;">Date</th>
                    <th style="padding:5px 8px;text-align:left;border-bottom:1px solid #ddd;
                               font-weight:bold;">Time</th>
                    <th style="padding:5px 8px;text-align:left;border-bottom:1px solid #ddd;
                               font-weight:bold;">Categories</th>
                </tr>
                {show_rows}
            </table>"""

        shows_html += f"""
        <h3 style="margin:20px 0 8px 0;font-size:16px;font-weight:bold;color:#111;
                   padding-bottom:6px;border-bottom:2px solid #333;">
            🎬 {escape(mname)}
        </h3>
        {venues_html}"""

    html = f"""<!doctype html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:24px;font-family:Arial,Helvetica,sans-serif;
             font-size:14px;color:#333;background:#fff;">
    <h2 style="margin:0 0 4px 0;font-size:18px;color:#111;">
        BMS Alert: {escape(header_title)}
    </h2>
    <p style="margin:0 0 20px 0;font-size:13px;color:#666;">
        {escape(now_str)}
    </p>
    <hr style="border:none;border-top:1px solid #ddd;margin:0 0 20px 0;">
    {changes_html}
    <h3 style="margin:0 0 8px 0;font-size:15px;font-weight:bold;color:#333;">
        Current Showtimes
    </h3>
    {shows_html}
    <p style="margin:24px 0 0 0;font-size:12px;color:#999;">
        This is an automated alert from BMS Ticket Notifier.
    </p>
</body>
</html>"""

    # Build plain-text version with full show details
    plain_lines = [subject, "", f"Checked at: {now_str}", ""]
    if changes:
        plain_lines.append("Changes Detected:")
        plain_lines.extend(f"  - {c}" for c in changes)
        plain_lines.append("")
    plain_lines.append("Current Showtimes:")
    for event_code, mshows in movie_groups.items():
        mname = movies.get(event_code, {}).get("name", "Unknown Movie")
        plain_lines.append(f"\n=== {mname} ===")

        venue_groups = {}
        for s in mshows:
            venue_groups.setdefault(s.venue_name, []).append(s)

        for vname, vshows in venue_groups.items():
            plain_lines.append(f"\n{vname}")
            vshows_sorted = sorted(vshows, key=lambda s: (s.date_code, s.time_code))
            for s in vshows_sorted:
                cats = " | ".join(
                    f"{c.name} Rs.{c.price} ({_cat_status_label(c.status)})"
                    for c in s.categories
                )
                fmt = f" [{s.screen_attr}]" if s.screen_attr else ""
                plain_lines.append(f"  [{_fmt_date(s.date_code)}] {s.time}{fmt} - {cats}")
    plain_lines.extend(["", "This is an automated alert from BMS Ticket Notifier."])
    plain = "\n".join(plain_lines)

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": frm, "to": [to],
                "subject": subject,
                "text": plain, "html": html,
            },
            timeout=15,
        )
        if resp.status_code in (200, 201):
            print(f"  ✅ Email sent to {to}")
        else:
            print(f"  ❌ Resend {resp.status_code}: {resp.text}")
            sys.exit(1)
    except requests.RequestException as e:
        print(f"  ❌ Email failed: {e}")
        sys.exit(1)


# ──────────────────────────────────────────────────────────────────────
# PUSH NOTIFICATION (ntfy.sh)
# ──────────────────────────────────────────────────────────────────────
def send_ntfy(title, message, url=None):
    topic = NTFY_TOPIC.strip()
    if not topic:
        print("  ⚠️  Skipping push — NTFY_TOPIC not set.")
        return

    headers = {
        "Title": title.encode("utf-8"),
        "Priority": "high",
        "Tags": "ticket",
    }
    if url:
        headers["Click"] = url

    try:
        resp = requests.post(
            f"{NTFY_SERVER.rstrip('/')}/{topic}",
            data=message.encode("utf-8"),
            headers=headers,
            timeout=15,
        )
        if resp.status_code == 200:
            print(f"  ✅ Push sent to ntfy topic '{topic}'")
        else:
            print(f"  ❌ ntfy {resp.status_code}: {resp.text}")
    except requests.RequestException as e:
        print(f"  ❌ Push failed: {e}")


# ──────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────
def main():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] BMS Ticket Checker — CI mode (multi-URL)")

    # Parse URLs from config (comma-separated)
    url_string = CONFIG["urls"].strip()
    urls = [u.strip() for u in url_string.split(",") if u.strip()]

    if not urls:
        print("  ❌ No URLs configured in BMS_URLS")
        sys.exit(1)

    print(f"  🔍 Checking {len(urls)} URL(s)")

    all_shows = []
    all_dates = []
    movies_found = {}  # event_code -> movie_info

    # Process each URL
    for url in urls:
        print(f"\n  📌 Processing: {url}")
        parsed = parse_bms_url(url)
        event_code = parsed["event_code"]
        region_slug = parsed["region_slug"]
        url_date = parsed.get("date_code", "")

        if not event_code or not region_slug:
            print(f"    ⚠️  Could not extract event/region from URL")
            continue

        region_code, region_slug_r, lat, lon, geohash = resolve_region(
            region_slug
        )

        # Determine dates to check
        raw_dates = CONFIG["dates"].strip()
        if raw_dates:
            date_list = [d.strip() for d in raw_dates.split(",") if d.strip()]
        elif url_date:
            date_list = [url_date]
        else:
            date_list = [""]

        print(f"    Event: {event_code}  Region: {region_code}  "
              f"Dates: {date_list}")

        # Fetch data for each date
        for dc in date_list:
            data = fetch_bms(event_code, dc, region_code,
                             region_slug_r, lat, lon, geohash)
            if not data:
                print(f"    ⚠️  No data for date {dc or '(default)'}")
                continue

            if event_code not in movies_found:
                movies_found[event_code] = parse_movie_info(data)

            all_dates.extend(parse_dates(data))
            all_shows.extend(parse_shows(data, event_code))

        if event_code in movies_found:
            info = movies_found[event_code]
            print(f"    🎬 {info['name']}  {info['language']}")

    if not all_shows:
        print("\n  ❌ No showtimes found across all URLs.")
        sys.exit(0)

    print(f"\n  📊 {len(all_shows)} showtime(s) total before filters")

    # Apply filters
    filtered = filter_shows(
        all_shows,
        CONFIG["theatre"],
        CONFIG["time_period"],
        CONFIG["dates"],
    )
    print(f"  🎯 {len(filtered)} showtime(s) after filters")

    # Build state & detect changes
    new_state = build_state(filtered, all_dates)
    old_state = load_state()

    changes = []
    if old_state:
        changes = detect_changes(old_state, new_state)

    save_state(new_state)

    if changes:
        print(f"\n  ⚡ {len(changes)} change(s) detected:")
        for c in changes:
            print(f"     {c}")

        movie_names = [m["name"] for m in movies_found.values()] or ["Movie"]
        subject_movie = movie_names[0] if len(movie_names) == 1 else f"{len(movie_names)} movies"
        send_email(
            f"BMS Alert: {subject_movie} - {len(changes)} change(s)",
            changes, filtered, movies_found,
        )

        new_shows = {
            k: v for k, v in new_state.get("shows", {}).items()
            if k not in old_state.get("shows", {})
        }
        if new_shows:
            groups = {}
            for v in new_shows.values():
                gkey = (v["event"], v["date"], v["venue"])
                groups.setdefault(gkey, set()).add(v["time"])

            lines = []
            for (event, date, venue), times in sorted(groups.items()):
                movie_name = movies_found.get(event, {}).get("name", "Movie")
                count = len(times)
                lines.append(
                    f"{movie_name}: {count} new show"
                    f"{'s' if count != 1 else ''} added for "
                    f"{_fmt_date(date)} @ {venue}"
                )

            push_title = (
                lines[0] if len(lines) == 1
                else f"{len(lines)} new showtime update(s)"
            )
            send_ntfy(push_title, "\n".join(lines))
    else:
        print("  ✅ No changes since last check.")

    # Print current status grouped by event
    print(f"\n  Current status ({len(filtered)} shows):")
    for event_code in sorted(set(s.event_code for s in filtered)):
        event_shows = [s for s in filtered if s.event_code == event_code]
        movie_name = movies_found.get(event_code, {}).get("name", "Unknown")
        print(f"    {movie_name} ({event_code})")
        for s in event_shows:
            cats = ", ".join(
                f"{c.name}=₹{c.price}({AVAIL_STATUS_MAP.get(c.status, ('?',''))[0]})"
                for c in s.categories
            )
            fmt = f"|{s.screen_attr}" if s.screen_attr else ""
            print(f"      {s.venue_name} — {s.time}{fmt} [{s.date_code}] — {cats}")

    print("\n  Done.")


if __name__ == "__main__":
    main()
