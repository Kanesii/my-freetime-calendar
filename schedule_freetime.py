#!/usr/bin/env python3
"""
schedule_freetime.py

Fetches a work-schedule ICS feed (e.g. the "Push" calendar link), figures out
free time within a defined waking-hours window, and writes a new ICS file
containing personal-time blocks that add up to a weekly hour target.

Configuration is via environment variables (set as GitHub Actions secrets/vars,
or just export them locally when testing):

  PUSH_ICS_URL        Required. The URL of your work schedule ICS feed.
  WEEKLY_HOURS_TARGET Default 6. Total hours of personal time to schedule per week.
  WAKE_START          Default "07:00". Earliest time of day personal time can start.
  WAKE_END            Default "23:00". Latest time of day personal time can end.
  MIN_BLOCK_MINUTES   Default 30. Ignore gaps shorter than this.
  MAX_BLOCK_MINUTES   Default 120. Cap on a single personal-time block.
  LOOKAHEAD_DAYS      Default 21. How many days ahead (from today) to plan.
  TIMEZONE            Default "America/Chicago". Your local IANA timezone.
  EVENT_TITLE         Default "Personal Time".
  OUTPUT_PATH         Default "personal_time.ics".
"""

import os
import sys
import hashlib
from datetime import datetime, timedelta, time as dtime

import requests
import pytz
from icalendar import Calendar, Event
import recurring_ical_events


def env_time(name, default):
    val = os.environ.get(name, default)
    h, m = val.split(":")
    return dtime(int(h), int(m))


def load_config():
    cfg = {
        "push_ics_url": os.environ.get("PUSH_ICS_URL"),
        "weekly_hours_target": float(os.environ.get("WEEKLY_HOURS_TARGET", "6")),
        "wake_start": env_time("WAKE_START", "07:00"),
        "wake_end": env_time("WAKE_END", "23:00"),
        "min_block_minutes": int(os.environ.get("MIN_BLOCK_MINUTES", "30")),
        "max_block_minutes": int(os.environ.get("MAX_BLOCK_MINUTES", "120")),
        "lookahead_days": int(os.environ.get("LOOKAHEAD_DAYS", "21")),
        "timezone": os.environ.get("TIMEZONE", "America/Chicago"),
        "event_title": os.environ.get("EVENT_TITLE", "Personal Time"),
        "output_path": os.environ.get("OUTPUT_PATH", "personal_time.ics"),
        "buffer_minutes": int(os.environ.get("BUFFER_MINUTES", "30")),
        "personal_ics_url": os.environ.get("PERSONAL_ICS_URL", "").strip(),
    }
    if not cfg["push_ics_url"]:
        sys.exit("PUSH_ICS_URL is not set. Set it as an environment variable or secret.")
    return cfg


def fetch_busy_intervals(ics_url, tz, start, end):
    """Download the work ICS feed and return a sorted list of (start, end) busy tuples,
    with recurring events already expanded."""
    resp = requests.get(ics_url, timeout=30)
    resp.raise_for_status()
    cal = Calendar.from_ical(resp.content)

    events = recurring_ical_events.of(cal).between(start, end)

    busy = []
    for e in events:
        dtstart = e["DTSTART"].dt
        dtend = e["DTEND"].dt if "DTEND" in e else dtstart

        # Normalize all-day (date-only) events to full-day datetime ranges
        if not isinstance(dtstart, datetime):
            dtstart = tz.localize(datetime.combine(dtstart, dtime.min))
        if not isinstance(dtend, datetime):
            dtend = tz.localize(datetime.combine(dtend, dtime.min))

        if dtstart.tzinfo is None:
            dtstart = tz.localize(dtstart)
        else:
            dtstart = dtstart.astimezone(tz)
        if dtend.tzinfo is None:
            dtend = tz.localize(dtend)
        else:
            dtend = dtend.astimezone(tz)

        busy.append((dtstart, dtend))

    busy.sort(key=lambda t: t[0])

    # Merge overlapping/adjacent busy intervals
    merged = []
    for s, e in busy:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def free_gaps_for_day(day_date, tz, wake_start, wake_end, busy_intervals):
    """Return free gaps on a single day, clipped to the waking-hours window."""
    day_start = tz.localize(datetime.combine(day_date, wake_start))
    day_end = tz.localize(datetime.combine(day_date, wake_end))
    if day_end <= day_start:
        return []

    todays_busy = [
        (max(s, day_start), min(e, day_end))
        for s, e in busy_intervals
        if e > day_start and s < day_end
    ]
    todays_busy.sort()

    gaps = []
    cursor = day_start
    for s, e in todays_busy:
        if s > cursor:
            gaps.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < day_end:
        gaps.append((cursor, day_end))
    return gaps


def allocate_blocks(gaps, minutes_needed, min_block, max_block):
    """Greedily place blocks (chronologically) into gaps until minutes_needed is used up."""
    blocks = []
    remaining = minutes_needed
    for start, end in sorted(gaps, key=lambda g: g[0]):
        if remaining <= 0:
            break
        gap_minutes = (end - start).total_seconds() / 60
        if gap_minutes < min_block:
            continue
        block_minutes = min(gap_minutes, max_block, remaining)
        if block_minutes < min_block:
            continue
        block_start = start
        block_end = start + timedelta(minutes=block_minutes)
        blocks.append((block_start, block_end))
        remaining -= block_minutes
    return blocks


def build_calendar(all_blocks, title):
    cal = Calendar()
    cal.add("prodid", "-//freetime-scheduler//personal-time//")
    cal.add("version", "2.0")
    cal.add("X-WR-CALNAME", title)

    for start, end in all_blocks:
        ev = Event()
        ev.add("summary", title)
        ev.add("dtstart", start)
        ev.add("dtend", end)
        # Deterministic UID so re-running doesn't create duplicate events
        raw = f"{start.isoformat()}-{end.isoformat()}-{title}"
        uid = hashlib.sha1(raw.encode()).hexdigest()
        ev.add("uid", f"{uid}@freetime-scheduler")
        cal.add_component(ev)

    return cal


def main():
    cfg = load_config()
    tz = pytz.timezone(cfg["timezone"])

    today = tz.localize(datetime.combine(datetime.now(tz).date(), dtime.min))
    horizon = today + timedelta(days=cfg["lookahead_days"])

    work_busy = fetch_busy_intervals(cfg["push_ics_url"], tz, today, horizon)

    # Pad each shift with a buffer on both sides so personal time never
    # gets scheduled immediately before or after work. The buffer itself
    # is left free (not written to the output calendar as an event).
    if cfg["buffer_minutes"] > 0:
        pad = timedelta(minutes=cfg["buffer_minutes"])
        padded = sorted((s - pad, e + pad) for s, e in work_busy)
        merged_padded = []
        for s, e in padded:
            if merged_padded and s <= merged_padded[-1][1]:
                merged_padded[-1] = (merged_padded[-1][0], max(merged_padded[-1][1], e))
            else:
                merged_padded.append((s, e))
        work_busy = merged_padded

    # Pull in a second calendar of events you added by hand (e.g. an
    # "Apple Calendar public share link"), and treat those as busy too,
    # no buffer applied since they're not work shifts.
    personal_busy = []
    if cfg["personal_ics_url"]:
        personal_busy = fetch_busy_intervals(cfg["personal_ics_url"], tz, today, horizon)

    busy = sorted(work_busy + personal_busy)
    merged_all = []
    for s, e in busy:
        if merged_all and s <= merged_all[-1][1]:
            merged_all[-1] = (merged_all[-1][0], max(merged_all[-1][1], e))
        else:
            merged_all.append((s, e))
    busy = merged_all

    all_blocks = []
    # Plan week by week (Mon-Sun) within the lookahead window
    day = today
    while day < horizon:
        week_start = day
        week_end = min(week_start + timedelta(days=7), horizon)

        week_gaps = []
        d = week_start
        while d < week_end:
            week_gaps.extend(
                free_gaps_for_day(d.date(), tz, cfg["wake_start"], cfg["wake_end"], busy)
            )
            d += timedelta(days=1)

        # Don't schedule personal time in the past on the current (partial) first week
        week_gaps = [(s, e) for s, e in week_gaps if e > datetime.now(tz)]

        minutes_target = cfg["weekly_hours_target"] * 60
        week_blocks = allocate_blocks(
            week_gaps, minutes_target, cfg["min_block_minutes"], cfg["max_block_minutes"]
        )
        all_blocks.extend(week_blocks)

        day = week_end

    cal = build_calendar(all_blocks, cfg["event_title"])

    with open(cfg["output_path"], "wb") as f:
        f.write(cal.to_ical())

    total_hours = sum((e - s).total_seconds() for s, e in all_blocks) / 3600
    print(f"Wrote {len(all_blocks)} blocks totaling {total_hours:.1f} hours to {cfg['output_path']}")


if __name__ == "__main__":
    main()
