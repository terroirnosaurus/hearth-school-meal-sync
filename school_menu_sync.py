#!/usr/bin/env python3
"""Turn Health-e Pro school menus into an iCalendar (.ics) feed.

Menus are read from the public JSON API behind menus.healthepro.com, the same
data the menu website renders. Standard library only, so it runs anywhere
Python 3.9+ is installed (including GitHub Actions) with nothing to install.

Usage:
    python3 school_menu_sync.py                 # write the .ics named in config.json
    python3 school_menu_sync.py --preview       # print the events instead
    python3 school_menu_sync.py --config other.json --output out.ics
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

API_BASE = "https://menus.healthepro.com/api/organizations"
USER_AGENT = "school-meal-calendar-sync/1.0"

# Fixed VTIMEZONE blocks for the zones we are likely to need. Clients such as
# Google Calendar accept a bare TZID, but RFC 5545 requires the definition.
VTIMEZONES = {
    "America/Los_Angeles": ("PST", "-0800", "PDT", "-0700"),
    "America/Denver": ("MST", "-0700", "MDT", "-0600"),
    "America/Chicago": ("CST", "-0600", "CDT", "-0500"),
    "America/New_York": ("EST", "-0500", "EDT", "-0400"),
}


@dataclass
class Event:
    uid: str
    day: date
    summary: str
    description: str = ""
    start: str | None = None  # "HH:MM"; None means an all-day event
    end: str | None = None
    categories: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- API


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def find_menu(menus: list[dict], name_contains: str) -> dict:
    """Pick the menu whose name matches, preferring the newest (highest id).

    Matching by name instead of pinning a menu id means the feed keeps working
    when the district publishes next school year's menu.
    """
    matches = [m for m in menus if name_contains.lower() in (m.get("name") or "").lower()]
    if not matches:
        names = ", ".join(m.get("name", "?") for m in menus)
        raise LookupError(f"No menu name contains {name_contains!r}. Available: {names}")
    return max(matches, key=lambda m: m["id"])


def fetch_month(org_id: int, menu_id: int, year: int, month: int) -> list[dict]:
    url = f"{API_BASE}/{org_id}/menus/{menu_id}/year/{year}/month/{month}/date_overwrites"
    return fetch_json(url)["data"]


# ----------------------------------------------------------------------- parsing


@dataclass
class DayMenu:
    day: date
    day_off: str | None  # description when there is no school, e.g. "Labor Day"
    items: dict[str, list[str]]  # category -> item names, in display order


def parse_day(entry: dict) -> DayMenu:
    setting = entry["setting"]
    if isinstance(setting, str):
        setting = json.loads(setting)

    days_off = setting.get("days_off") or {}
    day_off = days_off.get("description") or "No School" if days_off.get("status") else None

    items: dict[str, list[str]] = {}
    category = "Other"
    for row in sorted(setting.get("current_display") or [], key=lambda r: r.get("weight", 0)):
        if row.get("type") == "category":
            category = row["name"]
        elif row.get("type") == "recipe":
            items.setdefault(category, []).append(row["name"])

    return DayMenu(date.fromisoformat(entry["day"]), day_off, items)


def clean_name(name: str, rules: list[list[str]]) -> str:
    for pattern, replacement in rules:
        name = re.sub(pattern, replacement, name, flags=re.IGNORECASE)
    return name.strip()


def pick_emoji(text: str, rules: list[list[str]], default: str) -> str:
    for pattern, emoji in rules:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return emoji
    return default


def build_events(meal_cfg: dict, days: list[DayMenu], config: dict) -> list[Event]:
    cleanup = config.get("name_cleanup", [])
    title_cats = config.get("title_categories", [])
    desc_cats = config.get("description_categories", [])
    label = meal_cfg["label"]

    events = []
    for dm in days:
        if dm.day_off or not dm.items:
            continue

        def names(cat: str) -> list[str]:
            return list(dict.fromkeys(clean_name(n, cleanup) for n in dm.items.get(cat, [])))

        entrees = [n for cat in title_cats for n in names(cat)]
        if not entrees:  # menu without the expected entree category: use the first one listed
            entrees = names(next(iter(dm.items)))
        emoji = pick_emoji(entrees[0], config.get("food_emoji", []), meal_cfg.get("emoji", ""))

        lines = [f"{label}: " + " or ".join(entrees)]
        lines += [f"{cat}: " + ", ".join(names(cat)) for cat in desc_cats if dm.items.get(cat)]

        events.append(
            Event(
                uid=f"{dm.day.isoformat()}-{label.lower()}@school-meal-sync",
                day=dm.day,
                summary=f"{emoji} {label}: " + " or ".join(entrees),
                description="\n".join(lines),
                start=meal_cfg.get("start"),
                end=meal_cfg.get("end"),
                categories=[label],
            )
        )
    return events


def build_day_off_events(days: list[DayMenu]) -> list[Event]:
    seen: dict[date, Event] = {}
    for dm in days:
        if dm.day_off and dm.day not in seen:
            seen[dm.day] = Event(
                uid=f"{dm.day.isoformat()}-no-school@school-meal-sync",
                day=dm.day,
                summary=f"🏠 No School Meals: {dm.day_off}",
                categories=["No School"],
            )
    return list(seen.values())


# ------------------------------------------------------------------------ iCal


def ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line: str) -> str:
    """Fold to 75 octets per RFC 5545 without splitting a UTF-8 character."""
    out, current, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        limit = 75 if not out else 74  # continuation lines start with a space
        if size + n > limit:
            out.append(current)
            current, size = "", 0
        current += ch
        size += n
    out.append(current)
    return "\r\n ".join(out)


def vtimezone(tz: str) -> list[str]:
    if tz not in VTIMEZONES:
        return []
    std_name, std_off, dst_name, dst_off = VTIMEZONES[tz]
    return [
        "BEGIN:VTIMEZONE", f"TZID:{tz}",
        "BEGIN:DAYLIGHT", f"TZOFFSETFROM:{std_off}", f"TZOFFSETTO:{dst_off}", f"TZNAME:{dst_name}",
        "DTSTART:19700308T020000", "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU", "END:DAYLIGHT",
        "BEGIN:STANDARD", f"TZOFFSETFROM:{dst_off}", f"TZOFFSETTO:{std_off}", f"TZNAME:{std_name}",
        "DTSTART:19701101T020000", "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU", "END:STANDARD",
        "END:VTIMEZONE",
    ]


def render_ics(events: list[Event], name: str, tz: str) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//school-meal-sync//Health-e Pro menus//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(name)}",
        f"X-WR-TIMEZONE:{tz}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
        *vtimezone(tz),
    ]
    for ev in sorted(events, key=lambda e: (e.day, e.start or "", e.uid)):
        ymd = ev.day.strftime("%Y%m%d")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{ev.uid}",
            # Derived from the event date (not "now") so unchanged menus produce a
            # byte-identical file and the scheduled job only commits real changes.
            f"DTSTAMP:{ymd}T000000Z",
        ]
        if ev.start and ev.end:
            lines += [
                f"DTSTART;TZID={tz}:{ymd}T{ev.start.replace(':', '')}00",
                f"DTEND;TZID={tz}:{ymd}T{ev.end.replace(':', '')}00",
            ]
        else:
            next_day = (ev.day + timedelta(days=1)).strftime("%Y%m%d")
            lines += [f"DTSTART;VALUE=DATE:{ymd}", f"DTEND;VALUE=DATE:{next_day}"]
        lines.append(f"SUMMARY:{ics_escape(ev.summary)}")
        if ev.description:
            lines.append(f"DESCRIPTION:{ics_escape(ev.description)}")
        if ev.categories:
            lines.append("CATEGORIES:" + ",".join(ics_escape(c) for c in ev.categories))
        lines += ["TRANSP:TRANSPARENT", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


# ------------------------------------------------------------------------- main


def collect_events(config: dict) -> list[Event]:
    org, site = config["organization_id"], config["site_id"]
    menus = fetch_json(f"{API_BASE}/{org}/sites/{site}/menus/")["data"]

    events: list[Event] = []
    all_days: list[DayMenu] = []
    for meal_cfg in config["meals"]:
        menu = find_menu(menus, meal_cfg["menu_name_contains"])
        days: list[DayMenu] = []
        for month in menu.get("published_months") or []:
            first = date.fromisoformat(month)
            days += [parse_day(e) for e in fetch_month(org, menu["id"], first.year, first.month)]
        print(f"  {menu['name']}: {len(days)} days across {len(menu.get('published_months') or [])} month(s)",
              file=sys.stderr)
        events += build_events(meal_cfg, days, config)
        all_days += days

    if config.get("include_days_off", True):
        events += build_day_off_events(all_days)
    return events


def main() -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(here / "config.json"))
    parser.add_argument("--output", help="override the output path from config")
    parser.add_argument("--preview", action="store_true", help="print events instead of writing the file")
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text())
    print("Fetching menus…", file=sys.stderr)
    events = collect_events(config)

    # Refuse to publish an empty calendar: a transient API problem should leave
    # the last good feed in place rather than wipe the family calendar.
    if not any("No School" not in e.categories for e in events):
        print("ERROR: no meal events found; leaving existing calendar untouched.", file=sys.stderr)
        return 1

    if args.preview:
        for ev in sorted(events, key=lambda e: (e.day, e.start or "")):
            when = f"{ev.start}-{ev.end}" if ev.start else "all day"
            print(f"{ev.day:%a %b %d}  {when:<11}  {ev.summary}")
            for line in ev.description.splitlines()[1:]:
                print(f"{'':30}{line}")
        return 0

    out = Path(args.output or here / config["output"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(render_ics(events, config["calendar_name"], config["timezone"]).encode("utf-8"))
    print(f"Wrote {len(events)} events to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
