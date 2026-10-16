#!/usr/bin/env python3
"""Watch a Swiftqueue booking page and push a phone notification (via ntfy)
when an appointment slot is available within the next N hours.

Usage:
    python watcher.py --once              # single check (cron / GitHub Actions)
    python watcher.py                     # loop forever, checking every --interval seconds
    python watcher.py --once --debug      # also save the fetched page to debug/page.html
    python watcher.py --test-notify       # send a test push to your phone and exit

Booking pages to watch, first match wins:
    --url URL     (repeatable)
    BOOKING_URLS  env var, one URL per line
    urls.txt      one URL per line, # comments allowed

Other configuration comes from environment variables (or CLI flags):
    NTFY_TOPIC    your ntfy topic name (subscribe to it in the ntfy phone app)
    NTFY_SERVER   optional, defaults to https://ntfy.sh
    WINDOW_HOURS  optional, defaults to 48

Page structure this relies on (Swiftqueue timescreen.php, Oct 2026). Every bookable
slot for the next few weeks is in the server-rendered HTML; booked times are omitted:

    <div id="timescreen-header" ...>
      <h3><span class="fa fa-hospital-o"></span> Farnham Hospital Blood Tests </h3>
    ...
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
URLS_FILE = Path(__file__).with_name("urls.txt")
DEBUG_DIR = Path("debug")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)


class TimescreenParser(HTMLParser):
    """Collects the facility name and (date, time) pairs from the timescreen markup."""

    def __init__(self) -> None:
        super().__init__()
        self.name = ""
        self.days: list[str] = []
        self.slots: list[datetime] = []
        self._date: str | None = None
        self._in_header = False
        self._name_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if a.get("id") == "timescreen-header":
            self._in_header = True
        elif tag == "h3" and self._in_header and not self.name:
            self._name_parts = []
        elif "timescreen-day" in classes and a.get("data-date"):
            self._date = a["data-date"]
            self.days.append(self._date)
        elif "timescreen-time" in classes and a.get("data-time") and self._date:
            try:
                dt = datetime.strptime(f"{self._date} {a['data-time']}", "%Y-%m-%d %H:%M")
            except ValueError:
                return
            self.slots.append(dt.replace(tzinfo=TZ))

    def handle_data(self, data: str) -> None:
        if self._name_parts is not None:
            self._name_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3" and self._name_parts is not None:
            self.name = " ".join("".join(self._name_parts).split())
            self._name_parts = None
            self._in_header = False


def parse(html: str) -> tuple[str, list[str], list[datetime]]:
    p = TimescreenParser()
    p.feed(html)
    return p.name, p.days, sorted(set(p.slots))


def load_urls(cli_urls: list[str] | None) -> list[str]:
    if cli_urls:
        return cli_urls
    if os.environ.get("BOOKING_URLS", "").strip():
        text = os.environ["BOOKING_URLS"]
    elif URLS_FILE.exists():
        text = URLS_FILE.read_text()
    else:
        return []
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


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


def check(url: str, topic: str | None, window_hours: float, debug: bool, n: int = 1) -> int:
    now = datetime.now(TZ)
    html = fetch(url)
    if debug:
        DEBUG_DIR.mkdir(exist_ok=True)
        (DEBUG_DIR / f"page{n}.html").write_text(html, encoding="utf-8")
    name, days, slots = parse(html)
    name = name or f"booking page {n}"
    soon = [s for s in slots if now <= s <= now + timedelta(hours=window_hours)]

    print(f"[{now:%Y-%m-%d %H:%M}] {name}: {len(slots)} slots over {len(days)} days"
          f"{f' ({days[0]} to {days[-1]})' if days else ''}; within {window_hours:g}h: {len(soon)}")
    if slots:
        print(f"  earliest: {slots[0]:%a %d %b %H:%M}")
    for s in soon:
        print(f"  * {s:%a %d %b %H:%M}")

    state = load_state()
    cutoff = (now - timedelta(days=3)).isoformat()
    state["notified"] = {k: v for k, v in state["notified"].items() if v > cutoff}

    key = lambda s: f"{url}|{s.isoformat()}"  # noqa: E731
    new = [s for s in soon if key(s) not in state["notified"]]
    if new and topic:
        shown = new[:10]
        lines = "\n".join(f"{s:%a %d %b at %H:%M}" for s in shown)
        if len(new) > len(shown):
            lines += f"\n…and {len(new) - len(shown)} more"
        notify(topic, f"Blood test slot{'s' if len(new) > 1 else ''} at {name}",
               f"{lines}\n\nTap to book.", click=url, priority="urgent")
        for s in new:
            state["notified"][key(s)] = now.isoformat()
        print(f"  notified about {len(new)} new slot(s)")

    # No day blocks at all means the page layout changed (or the link expired).
    # Alert at most once a day so a broken watcher doesn't fail silently.
    if not days and topic:
        last = state["alerts"].get(url, "")
        if last < (now - timedelta(days=1)).isoformat():
            notify(topic, "Blood test watcher needs attention",
                   f"Couldn't find any appointment days on {name}. The link may have "
                   "expired or the page layout changed. Run: python watcher.py --once --debug",
                   click=url, priority="default")
            state["alerts"][url] = now.isoformat()

    save_state(state)
    return len(soon)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", action="append", help="booking page URL (repeatable; default: urls.txt)")
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
        notify(args.topic, "Blood test watcher", "Test notification: it works!")
        print("Sent.")
        return
    urls = load_urls(args.url)
    if not urls:
        sys.exit("No booking URLs: add them to urls.txt, set BOOKING_URLS, or pass --url")
    if not args.topic:
        print("WARNING: no NTFY_TOPIC set; will only print results.", file=sys.stderr)

    while True:
        failed = 0
        for n, url in enumerate(urls, 1):
            try:
                check(url, args.topic, args.hours, args.debug, n)
            except Exception as e:  # one bad page or network blip shouldn't stop the others
                failed += 1
                print(f"check failed for {url}: {e!r}", file=sys.stderr)
        if args.once:
            sys.exit(1 if failed else 0)
        time.sleep(args.interval + random.randint(0, 60))


if __name__ == "__main__":
    main()
