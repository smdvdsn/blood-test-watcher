#!/usr/bin/env python3
"""Watch a Swiftqueue booking page and push a phone notification (via ntfy)
when an appointment slot is available within the next N hours.

Usage:
    python watcher.py --once              # single check (cron / GitHub Actions)
    python watcher.py                     # loop forever, checking every --interval seconds
    python watcher.py --once --debug      # also save the fetched page to debug/page.html
    python watcher.py --test-notify       # send a test push to your phone and exit

Configuration comes from environment variables (or CLI flags):
    BOOKING_URL   the Swiftqueue timescreen.php URL
    NTFY_TOPIC    your ntfy topic name (subscribe to it in the ntfy phone app)
    NTFY_SERVER   optional, defaults to https://ntfy.sh
    WINDOW_HOURS  optional, defaults to 48

Page structure this relies on (Swiftqueue timescreen.php, Oct 2026). Every bookable
slot for the next few weeks is in the server-rendered HTML; booked times are omitted:

    <div class="timescreen-day" data-date="2026-11-19">
      <h3 class="date-title">Thu, 19 Nov</h3>
      ...
      <ul id="morning-times-1" class="time-container" style="display:none;">
        <li data-time="11:15" class="timescreen-time">11:15</li>
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/London")
STATE_FILE = Path(os.environ.get("STATE_FILE", "state.json"))
DEBUG_DIR = Path("debug")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)


class TimescreenParser(HTMLParser):
    """Collects (date, time) pairs from timescreen-day / timescreen-time elements."""

    def __init__(self) -> None:
        super().__init__()
        self.days: list[str] = []
        self.slots: list[datetime] = []
        self._date: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if "timescreen-day" in classes and a.get("data-date"):
            self._date = a["data-date"]
            self.days.append(self._date)
        elif "timescreen-time" in classes and a.get("data-time") and self._date:
            try:
                dt = datetime.strptime(f"{self._date} {a['data-time']}", "%Y-%m-%d %H:%M")
            except ValueError:
                return
            self.slots.append(dt.replace(tzinfo=TZ))


def parse(html: str) -> tuple[list[str], list[datetime]]:
    p = TimescreenParser()
    p.feed(html)
    return p.days, sorted(set(p.slots))


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-GB"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", errors="replace")


def notify(topic: str, title: str, message: str, click: str | None = None, priority: str = "high") -> None:
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    headers = {"Title": title, "Priority": priority, "Tags": "syringe"}
    if click:
        headers["Click"] = click
    req = urllib.request.Request(f"{server}/{topic}", data=message.encode(), headers=headers)
    urllib.request.urlopen(req, timeout=30).read()


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"notified": {}, "alerts": {}}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def check(url: str, topic: str | None, window_hours: float, debug: bool) -> int:
    now = datetime.now(TZ)
    html = fetch(url)
    if debug:
        DEBUG_DIR.mkdir(exist_ok=True)
        (DEBUG_DIR / "page.html").write_text(html, encoding="utf-8")
    days, slots = parse(html)
    soon = [s for s in slots if now <= s <= now + timedelta(hours=window_hours)]

    print(f"[{now:%Y-%m-%d %H:%M}] {len(slots)} slots over {len(days)} days"
          f"{f' ({days[0]} to {days[-1]})' if days else ''}; within {window_hours:g}h: {len(soon)}")
    if slots:
        print(f"  earliest: {slots[0]:%a %d %b %H:%M}")
    for s in soon:
        print(f"  * {s:%a %d %b %H:%M}")

    state = load_state()
    cutoff = (now - timedelta(days=3)).isoformat()
    state["notified"] = {k: v for k, v in state["notified"].items() if v > cutoff}

    new = [s for s in soon if s.isoformat() not in state["notified"]]
    if new and topic:
        shown = new[:10]
        lines = "\n".join(f"{s:%a %d %b at %H:%M}" for s in shown)
        if len(new) > len(shown):
            lines += f"\n…and {len(new) - len(shown)} more"
        notify(topic, f"Blood test slot{'s' if len(new) > 1 else ''} available!",
               f"{lines}\n\nTap to book.", click=url, priority="urgent")
        for s in new:
            state["notified"][s.isoformat()] = now.isoformat()
        print(f"  notified about {len(new)} new slot(s)")

    # No day blocks at all means the page layout changed (or the link expired).
    # Alert at most once a day so a broken watcher doesn't fail silently.
    if not days and topic:
        last = state["alerts"].get("no_days", "")
        if last < (now - timedelta(days=1)).isoformat():
            notify(topic, "Blood test watcher needs attention",
                   "Couldn't find any appointment days on the booking page. The link may have "
                   "expired or the page layout changed. Run: python watcher.py --once --debug",
                   click=url, priority="default")
            state["alerts"]["no_days"] = now.isoformat()

    save_state(state)
    return len(soon)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("BOOKING_URL"))
    ap.add_argument("--topic", default=os.environ.get("NTFY_TOPIC"))
    ap.add_argument("--hours", type=float, default=float(os.environ.get("WINDOW_HOURS", 48)))
    ap.add_argument("--interval", type=int, default=300, help="seconds between checks in loop mode")
    ap.add_argument("--once", action="store_true", help="check once and exit")
    ap.add_argument("--debug", action="store_true", help="save the fetched page to debug/page.html")
    ap.add_argument("--test-notify", action="store_true", help="send a test notification and exit")
    args = ap.parse_args()

    if args.test_notify:
        if not args.topic:
            sys.exit("Set NTFY_TOPIC or pass --topic")
        notify(args.topic, "Blood test watcher", "Test notification: it works!", click=args.url)
        print("Sent.")
        return
    if not args.url:
        sys.exit("Set BOOKING_URL or pass --url")
    if not args.topic:
        print("WARNING: no NTFY_TOPIC set; will only print results.", file=sys.stderr)

    while True:
        try:
            check(args.url, args.topic, args.hours, args.debug)
        except Exception as e:  # keep looping through transient network/site errors
            print(f"check failed: {e!r}", file=sys.stderr)
            if args.once:
                raise
        if args.once:
            return
        time.sleep(args.interval + random.randint(0, 60))


if __name__ == "__main__":
    main()
