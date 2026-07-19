#!/usr/bin/env python3
"""
mirror_work_caldav.py

Mirrors your Push work schedule into a real, editable iCloud calendar
(same CalDAV mechanism as the Comptia Study calendar), instead of a
read-only subscription. This lets you attach a Location to each shift,
which is what Apple's native "Time to Leave" feature (Settings > Calendar
> Time to Leave) needs to send you a live, transit-aware "leave now" alert.

This mirror always exactly matches Push -- every run wipes and recreates
all shifts fresh. No drag-protection, unlike Comptia Study: a real work
shift should never silently diverge from what Push actually says.

Environment variables:

  PUSH_ICS_URL          Required. Same Push link as the other scripts.
  APPLE_ID              Required. Your iCloud email.
  APPLE_APP_PASSWORD    Required. App-specific password from appleid.apple.com.
  WORK_CALENDAR_NAME    Default "Work". Name of the editable calendar.
  WORK_LOCATION         Optional. Address or place name attached to every
                         shift so Time to Leave can calculate transit time.
  LOOKAHEAD_DAYS        Default 9. How many days ahead to mirror.
  TIMEZONE              Default "America/New_York".
"""

import os
import sys
from datetime import datetime, timedelta, time as dtime

import caldav
import pytz
from icalendar import Calendar as ICalCalendar, Event as ICalEvent

from schedule_freetime import fetch_busy_intervals

UID_PREFIX = "work-mirror-"


def load_config():
    cfg = {
        "push_ics_url": os.environ.get("PUSH_ICS_URL"),
        "apple_id": os.environ.get("APPLE_ID"),
        "apple_app_password": os.environ.get("APPLE_APP_PASSWORD"),
        "work_calendar_name": os.environ.get("WORK_CALENDAR_NAME", "Work"),
        "work_location": os.environ.get("WORK_LOCATION", "").strip(),
        "lookahead_days": int(os.environ.get("LOOKAHEAD_DAYS", "9")),
        "timezone": os.environ.get("TIMEZONE", "America/New_York"),
    }
    missing = [k for k in ("push_ics_url", "apple_id", "apple_app_password") if not cfg[k]]
    if missing:
        sys.exit(f"Missing required config: {', '.join(missing)}")
    return cfg


def get_or_create_calendar(client, calendar_name):
    principal = client.principal()
    for cal in principal.calendars():
        if cal.name == calendar_name:
            return cal
    return principal.make_calendar(name=calendar_name)


def clear_mirrored_events(calendar):
    for ev in calendar.search(event=True):
        vevent = ev.icalendar_component
        uid = str(vevent.get("uid", ""))
        if uid.startswith(UID_PREFIX):
            ev.delete()


def create_event(calendar, uid, title, start, end, location):
    cal = ICalCalendar()
    cal.add("prodid", "-//freetime-scheduler//work-mirror//")
    cal.add("version", "2.0")
    vevent = ICalEvent()
    vevent.add("summary", title)
    vevent.add("dtstart", start)
    vevent.add("dtend", end)
    vevent.add("uid", uid)
    if location:
        vevent.add("location", location)
    cal.add_component(vevent)
    calendar.save_event(cal.to_ical().decode("utf-8"))


def main():
    cfg = load_config()
    tz = pytz.timezone(cfg["timezone"])

    today = tz.localize(datetime.combine(datetime.now(tz).date(), dtime.min))
    horizon = today + timedelta(days=cfg["lookahead_days"])

    shifts = fetch_busy_intervals(cfg["push_ics_url"], tz, today, horizon)

    client = caldav.DAVClient(
        url="https://caldav.icloud.com/",
        username=cfg["apple_id"],
        password=cfg["apple_app_password"],
    )
    calendar = get_or_create_calendar(client, cfg["work_calendar_name"])

    clear_mirrored_events(calendar)

    for i, (start, end) in enumerate(shifts):
        uid = f"{UID_PREFIX}{i}@freetime-scheduler"
        create_event(calendar, uid, "Work Shift", start, end, cfg["work_location"])

    total_hours = sum((e - s).total_seconds() for s, e in shifts) / 3600
    print(f"Mirrored {len(shifts)} shifts ({total_hours:.1f}h) to '{cfg['work_calendar_name']}'.")


if __name__ == "__main__":
    main()
