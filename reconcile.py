"""
reconcile.py

The core logic that lets you drag study blocks around in Apple Calendar
without the next automated run snapping them back.

How it works:
- After every run, we save a small state.json recording exactly what we
  last wrote: {uid, start, end} for every auto-generated block.
- On the next run, before touching anything, we check each of those UIDs
  against what's ACTUALLY on the calendar right now.
    - Still matches what we last wrote?  -> "untouched", safe to
      delete/regenerate as part of normal re-planning.
    - Exists but times differ from what we last wrote? -> you moved it.
      We treat its current (moved) time as busy time going forward, and
      we never touch that event again.
    - No longer exists at all? -> you deleted it. We don't recreate it.
"""

from dataclasses import dataclass, asdict
from datetime import datetime


@dataclass
class BlockState:
    uid: str
    start: str  # ISO format
    end: str
    user_owned: bool = False


def reconcile(previous_state, live_calendar_events):
    """
    previous_state: list of dicts, what we wrote last run
        [{"uid": ..., "start": iso, "end": iso, "user_owned": bool}, ...]
    live_calendar_events: dict of {uid: (start_iso, end_iso)} representing
        what is ACTUALLY on the calendar right now (only events with our
        UID prefix, i.e. events we manage)

    Returns:
        untouched_uids: set of UIDs safe to delete + regenerate
        user_owned_blocks: list of (start, end) to treat as busy and preserve
        deleted_uids: set of UIDs that vanished (user deleted, don't recreate)
    """
    untouched_uids = set()
    user_owned_blocks = []
    deleted_uids = set()

    for entry in previous_state:
        uid = entry["uid"]

        if entry.get("user_owned"):
            # Already flagged as user-owned in a prior run. Keep respecting
            # it as long as it still exists; if it's gone, the user deleted
            # it and we let it go.
            if uid in live_calendar_events:
                start, end = live_calendar_events[uid]
                user_owned_blocks.append((start, end))
            else:
                deleted_uids.add(uid)
            continue

        if uid not in live_calendar_events:
            deleted_uids.add(uid)
            continue

        live_start, live_end = live_calendar_events[uid]
        if live_start == entry["start"] and live_end == entry["end"]:
            untouched_uids.add(uid)
        else:
            # Times differ from what we last wrote -> moved by hand.
            user_owned_blocks.append((live_start, live_end))

    return untouched_uids, user_owned_blocks, deleted_uids
