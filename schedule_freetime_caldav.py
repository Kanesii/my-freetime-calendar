#!/usr/bin/env python3
"""
schedule_freetime_caldav.py

Same free-time planning as schedule_freetime.py, but instead of publishing a
static, read-only .ics file, it writes real, draggable events directly onto
an editable iCloud calendar via CalDAV.

If you move a block by hand in Apple Calendar, this script will notice
(via reconcile.py) and leave it alone on every future run, treating its new
time as busy so nothing else gets scheduled on top of it.

New/changed environment variables vs. schedule_freetime.py:

  APPLE_ID              Required. Your iCloud email address.
  APPLE_APP_PASSWORD     Required. An app-specific password from
                          appleid.apple.com (NOT your real Apple ID password).
  CALDAV_CALENDAR_NAME   Default: same as EVENT_TITLE. The name of the
                          editable calendar to create/use on iCloud.
  STATE_PATH             Default "state.json". Where the reconciliation
                          state is stored (commit this file to the repo,
                          same as personal_time.ics was committed before).

All the planning-related variables (WEEKLY_HOURS_TARGET, WAKE_START,
WAKE_END, BUFFER_MINUTES, MIN_BLOCK_MINUTES, MAX_BLOCK_MINUTES,
LOOKAHEAD_DAYS, TIMEZONE, PUSH_ICS_URL, PERSONAL_ICS_URL) work exactly as
before.
"""

import os
import sys
import json
import hashlib
from datetime import datetime, timedelta, time as dtime

import caldav
from icalendar import Calendar as ICalCalendar, Event as ICalEvent

# Reuse the exact same fetching/gap-finding/allocation logic as the
# original script -- nothing about the PLANNING changes, only how the
# result gets published.
from schedule_freetime import (
    fetch_busy_intervals,
    free_gaps_for_day,
    allocate_blocks,
    env_time,
)
from reconcile import reconcile

UID_PREFIX = "freetime-"


def load_config():
    cfg = {
        "push_ics_url": os.environ.get("PUSH_ICS_URL"),
        "personal_ics_url": os.environ.get("PERSONAL_ICS_URL", "").strip(),
        "weekly_hours_target": float(os.environ.get("WEEKLY_HOURS_TARGET", "6")),
        "wake_start": env_time("WAKE_START", "07:00"),
        "wake_end": env_time("WAKE_END", "23:00"),
        "min_block_minutes": int(os.environ.get("MIN_BLOCK_MINUTES", "30")),
        "max_block_minutes": int(os.environ.get("MAX_BLOCK_MINUTES", "120")),
        "lookahead_days": int(os.environ.get("LOOKAHEAD_DAYS", "21")),
        "timezone": os.environ.get("TIMEZONE", "America/Chicago"),
        "event_title": os.environ.get("EVENT_TITLE", "Personal Time"),
        "buffer_minutes": int(os.environ.get("BUFFER_MINUTES", "30")),
        "apple_id": os.environ.get("APPLE_ID"),
        "apple_app_password": os.environ.get("APPLE_APP_PASSWORD"),
        "state_path": os.environ.get("STATE_PATH", "state.json"),
    }
    cfg["caldav_calendar_name"] = os.environ.get("CALDAV_CALENDAR_NAME", cfg["event_title"])

    missing = [k for k in ("push_ics_url", "apple_id", "apple_app_password") if not cfg[k]]
    if missing:
        sys.exit(f"Missing required config: {', '.join(missing)}")
    return cfg


def load_state(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def save_state(path, state):
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def get_or_create_calendar(client, calendar_name):
    principal = client.principal()
    for cal in principal.calendars():
        if cal.name == calendar_name:
            return cal
    return principal.make_calendar(name=calendar_name)


def fetch_live_managed_events(calendar, tz, start, end):
    """Return {uid: (start_iso, end_iso)} for events we manage (UID prefix
    match) currently on the calendar within the given window."""
    live = {}
    events = calendar.search(start=start, end=end, event=True, expand=True)
    for ev in events:
        vevent = ev.icalendar_component
        uid = str(vevent.get("uid", ""))
        if not uid.startswith(UID_PREFIX):
            continue
        s = vevent["DTSTART"].dt
        e = vevent["DTEND"].dt
        if s.tzinfo is None:
            s = tz.localize(s)
        if e.tzinfo is None:
            e = tz.localize(e)
        live[uid] = (s.isoformat(), e.isoformat())
    return live


def delete_event(calendar, uid):
    for ev in calendar.search(event=True):
        vevent = ev.icalendar_component
        if str(vevent.get("uid", "")) == uid:
            ev.delete()
            return


def create_event(calendar, uid, title, start, end):
    cal = ICalCalendar()
    cal.add("prodid", "-//freetime-scheduler//caldav//")
    cal.add("version", "2.0")
    vevent = ICalEvent()
    vevent.add("summary", title)
    vevent.add("dtstart", start)
    vevent.add("dtend", end)
    vevent.add("uid", uid)
    cal.add_component(vevent)
    calendar.save_event(cal.to_ical().decode("utf-8"))


def make_uid(index):
    # Stable per-slot ID, independent of the block's time, so identity
    # survives you dragging it to a new time.
    return f"{UID_PREFIX}{index}@freetime-scheduler"


def main():
    cfg = load_config()
    import pytz

    tz = pytz.timezone(cfg["timezone"])
    today = tz.localize(datetime.combine(datetime.now(tz).date(), dtime.min))
    horizon = today + timedelta(days=cfg["lookahead_days"])

    # --- Step 1: connect to iCloud CalDAV ---
    client = caldav.DAVClient(
        url="https://caldav.icloud.com/",
        username=cfg["apple_id"],
        password=cfg["apple_app_password"],
    )
    calendar = get_or_create_calendar(client, cfg["caldav_calendar_name"])

    # --- Step 2: figure out what's actually on the calendar right now,
    # and reconcile against what we said we'd put there last run ---
    previous_state = load_state(cfg["state_path"])
    live_events = fetch_live_managed_events(calendar, tz, today, horizon)
    untouched_uids, user_owned_blocks, deleted_uids = reconcile(previous_state, live_events)

    print(f"Reconciliation: {len(untouched_uids)} untouched, "
          f"{len(user_owned_blocks)} user-moved (preserved), "
          f"{len(deleted_uids)} deleted by user (not recreated)")

    # --- Step 3: compute busy time from Push + Manual, same as before ---
    work_busy = fetch_busy_intervals(cfg["push_ics_url"], tz, today, horizon)
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

    personal_busy = []
    if cfg["personal_ics_url"]:
        personal_busy = fetch_busy_intervals(cfg["personal_ics_url"], tz, today, horizon)

    # User-moved blocks count as busy too, so nothing gets scheduled on
    # top of a block you deliberately relocated.
    user_owned_intervals = [
        (datetime.fromisoformat(s), datetime.fromisoformat(e))
        for s, e in user_owned_blocks
    ]

    busy = sorted(work_busy + personal_busy + user_owned_intervals)
    merged_all = []
    for s, e in busy:
        if merged_all and s <= merged_all[-1][1]:
            merged_all[-1] = (merged_all[-1][0], max(merged_all[-1][1], e))
        else:
            merged_all.append((s, e))
    busy = merged_all

    # --- Step 4: delete the untouched auto-generated events; we'll
    # regenerate fresh ones to fill the remaining target hours ---
    for uid in untouched_uids:
        delete_event(calendar, uid)

   # --- Step 5: plan new blocks around ALL busy time (work + manual +
    # user-moved blocks), reduced by hours already covered by user-owned
    # blocks ---
    # IMPORTANT: build the week windows ONCE and reuse the same list for
    # both bucketing owned hours and planning new blocks. Two independently
    # computed week-boundary schemes (e.g. one calendar-Sunday-aligned, one
    # "today plus 7 days" chunked) will not line up unless the script
    # happens to run on a Sunday, which silently drops the owned-hours
    # deduction and over-schedules.
    week_windows = []
    day = today
    while day < horizon:
        week_start = day
        week_end = min(week_start + timedelta(days=7), horizon)
        week_windows.append((week_start, week_end))
        day = week_end

    already_owned_minutes = [0.0] * len(week_windows)
    for s, e in user_owned_intervals:
        for i, (ws, we) in enumerate(week_windows):
            if ws <= s < we:
                already_owned_minutes[i] += (e - s).total_seconds() / 60
                break

    new_blocks = []
    for i, (week_start, week_end) in enumerate(week_windows):
        week_gaps = []
        d = week_start
        while d < week_end:
            week_gaps.extend(
                free_gaps_for_day(d.date(), tz, cfg["wake_start"], cfg["wake_end"], busy)
            )
            d += timedelta(days=1)

        week_gaps = [(s, e) for s, e in week_gaps if e > datetime.now(tz)]

        target_minutes = cfg["weekly_hours_target"] * 60
        remaining_minutes = max(0.0, target_minutes - already_owned_minutes[i])

        week_blocks = allocate_blocks(
            week_gaps, remaining_minutes, cfg["min_block_minutes"], cfg["max_block_minutes"]
        )
        new_blocks.extend(week_blocks)

    # --- Step 6: write the new auto-generated blocks to the calendar ---
    new_state = []

    # Carry forward user-owned entries as-is (with refreshed actual times)
    owned_uid_map = {}
    for entry in previous_state:
        if entry.get("user_owned") and entry["uid"] in live_events:
            s, e = live_events[entry["uid"]]
            new_state.append({"uid": entry["uid"], "start": s, "end": e, "user_owned": True})
            owned_uid_map[entry["uid"]] = True
        elif not entry.get("user_owned") and entry["uid"] not in untouched_uids and entry["uid"] in live_events:
            # This one was just detected as newly moved this run
            s, e = live_events[entry["uid"]]
            new_state.append({"uid": entry["uid"], "start": s, "end": e, "user_owned": True})

    next_index = 0
    existing_indices = set()
    for entry in previous_state:
        try:
            idx = int(entry["uid"].replace(UID_PREFIX, "").split("@")[0])
            existing_indices.add(idx)
        except ValueError:
            pass

    for s, e in new_blocks:
        while next_index in existing_indices:
            next_index += 1
        uid = make_uid(next_index)
        existing_indices.add(next_index)
        create_event(calendar, uid, cfg["event_title"], s, e)
        new_state.append({
            "uid": uid,
            "start": s.isoformat(),
            "end": e.isoformat(),
            "user_owned": False,
        })

    save_state(cfg["state_path"], new_state)

    total_new_hours = sum((e - s).total_seconds() for s, e in new_blocks) / 3600
    total_owned_hours = sum(
        (datetime.fromisoformat(en["end"]) - datetime.fromisoformat(en["start"])).total_seconds()
        for en in new_state if en["user_owned"]
    ) / 3600
    print(f"Created {len(new_blocks)} new blocks ({total_new_hours:.1f}h). "
          f"Preserving {total_owned_hours:.1f}h of manually-moved blocks. "
          f"{len(deleted_uids)} block(s) you deleted were not recreated.")


if __name__ == "__main__":
    main()
