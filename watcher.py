#!/usr/bin/env python3
"""Watch Swiftqueue booking pages and send a phone notification (ntfy and/or
WhatsApp) when there's a cancellation: a slot that is free now but wasn't at the
previous check.

Each check compares a page's free slots with the previous check's snapshot (in
state.json). New slots dated after the last day the previous check could see are
newly released days, not cancellations, so they're ignored. The first check of a
page just records the snapshot.

Usage:
    python watcher.py --once              # single check (cron / GitHub Actions)
    python watcher.py                     # loop forever, checking every --interval seconds
    python watcher.py --once --debug      # also save the fetched page to debug/page.html
    python watcher.py --test-notify       # send a test message on every channel and exit

Booking pages to watch, first match wins:
    --url URL     (repeatable)
    BOOKING_URLS  env var, one URL per line
    urls.txt      one URL per line, # comments allowed

Other configuration comes from environment variables (or CLI flags):
    NTFY_TOPIC        your ntfy topic name (subscribe to it in the ntfy phone app)
    NTFY_SERVER       optional, defaults to https://ntfy.sh
    WHATSAPP_PHONE    your WhatsApp number in international format, e.g. +447700900123
    WHATSAPP_APIKEY   your CallMeBot API key (see README)
    WINDOW_HOURS      optional: only report cancellations starting within this many hours

Page structure this relies on (Swiftqueue timescreen.php, Oct 2026). Every bookable
slot for the next few weeks is in the server-rendered HTML; booked times are omitted:

    <div id="timescreen-header" ...>
      <h3><span class="fa fa-hospital-o"></span> Farnham Hospital Blood Tests </h3>   (h2 on some pages)
    ...
    <div class="timescreen-day" data-date="2026-11-19">
      <h3 class="date-title">Thu, 19 Nov</h3>
      ...
      <ul id="morning-times-1" class="time-container" style="display:none;">
        <li data-time="11:15" class="timescreen-time">11:15</li>

A pre_timescreen.php link is an "appointment reason" page instead; the watcher follows
its Blood Test button, e.g. <a href="timescreen.php?id=...&rid=52"><h4>Blood Test</h4></a>.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlencode, urljoin
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
    """Collects the facility name, (date, time) pairs and any links on to timescreen.php."""

    def __init__(self) -> None:
        super().__init__()
        self.name = ""
        self.days: list[str] = []
        self.slots: list[datetime] = []
        self._date: str | None = None
        self._in_header = False
        self._name_parts: list[str] | None = None
        self.links: list[tuple[str, str]] = []  # (href, text) of links to timescreen.php
        self._link: tuple[str, list[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if a.get("id") == "timescreen-header":
            self._in_header = True
        elif tag in ("h2", "h3") and self._in_header and not self.name:
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
        elif tag == "a" and (a.get("href") or "").startswith("timescreen.php?"):
            self._link = (a["href"], [])

    def handle_data(self, data: str) -> None:
        if self._name_parts is not None:
            self._name_parts.append(data)
        if self._link:
            self._link[1].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("h2", "h3") and self._name_parts is not None:
            self.name = " ".join("".join(self._name_parts).split())
            self._name_parts = None
            self._in_header = False
        elif tag == "a" and self._link:
            self.links.append((self._link[0], " ".join("".join(self._link[1]).split())))
            self._link = None


def parse_page(html: str) -> TimescreenParser:
    p = TimescreenParser()
    p.feed(html)
    return p


def parse(html: str) -> tuple[str, list[str], list[datetime]]:
    p = parse_page(html)
    return p.name, p.days, sorted(set(p.slots))


def blood_test_link(html: str, base_url: str) -> str | None:
    """On a pre_timescreen.php reason page, the URL of the Blood Test option."""
    links = parse_page(html).links
    for href, text in links:
        if "blood" in text.lower():
            return urljoin(base_url, href)
    return urljoin(base_url, links[0][0]) if len(links) == 1 else None


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


def notify_whatsapp(phone: str, apikey: str, title: str, message: str, click: str | None = None) -> None:
    """Send a WhatsApp message to yourself via CallMeBot's free personal-use API."""
    text = f"*{title}*\n{message}"
    if click:
        # WhatsApp stops auto-linking at a literal "$", which Swiftqueue ids are full of.
        text += "\n" + quote(click, safe=":/?&=.%")
    url = "https://api.callmebot.com/whatsapp.php?" + urlencode({"phone": phone, "text": text, "apikey": apikey})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode("utf-8", errors="replace")
    # CallMeBot reports problems (bad key, unknown number) in a 200 response body.
    if "queued" not in body.lower() and any(w in body.lower() for w in ("error", "invalid")):
        raise RuntimeError(f"CallMeBot: {' '.join(re.sub(r'<[^>]+>', ' ', body).split())[:200]}")


class Notifier:
    """Sends each message to every configured channel."""

    def __init__(self, ntfy_topic: str | None = None, whatsapp_phone: str | None = None,
                 whatsapp_apikey: str | None = None) -> None:
        self.ntfy_topic = ntfy_topic
        self.whatsapp = (whatsapp_phone, whatsapp_apikey) if whatsapp_phone and whatsapp_apikey else None

    @property
    def channels(self) -> list[str]:
        return [name for name, on in (("ntfy", self.ntfy_topic), ("WhatsApp", self.whatsapp)) if on]

    def send(self, title: str, message: str, click: str | None = None, priority: str = "high") -> None:
        senders = []
        if self.ntfy_topic:
            senders.append(("ntfy", lambda: notify(self.ntfy_topic, title, message, click, priority)))
        if self.whatsapp:
            senders.append(("WhatsApp", lambda: notify_whatsapp(*self.whatsapp, title, message, click)))
        errors = []
        for name, send in senders:
            try:
                send()
            except Exception as e:
                errors.append(f"{name}: {e!r}")
                print(f"  {name} notification failed: {e!r}", file=sys.stderr)
        # Only fail if nothing got through, so the slots are retried on the next check
        # instead of being dropped.
        if senders and len(errors) == len(senders):
            raise RuntimeError("all notification channels failed: " + "; ".join(errors))


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, separators=(",", ":")))


def snapshot(slots: list[datetime], days: list[str]) -> dict:
    """Compact, JSON-friendly record of a page's free slots: {"YYYY-MM-DD": "HH:MM HH:MM ..."}."""
    by_day: dict[str, list[str]] = {d: [] for d in days}
    for s in slots:
        by_day.setdefault(f"{s:%Y-%m-%d}", []).append(f"{s:%H:%M}")
    return {"last_day": max(days) if days else "", "slots": {d: " ".join(t) for d, t in by_day.items()}}


def find_cancellations(prev: dict, slots: list[datetime], now: datetime) -> list[datetime]:
    """Slots free now that weren't at the previous check, excluding newly released days."""
    before = {f"{d} {t}" for d, times in prev["slots"].items() for t in times.split()}
    return [s for s in slots
            if s >= now and f"{s:%Y-%m-%d}" <= prev["last_day"] and f"{s:%Y-%m-%d %H:%M}" not in before]


def check(url: str, notifier: Notifier, window_hours: float | None, debug: bool, n: int = 1) -> int:
    now = datetime.now(TZ)
    page_url = url
    html = fetch(url)
    if "/pre_timescreen.php" in url:
        page_url = blood_test_link(html, url) or url
        if page_url != url:
            html = fetch(page_url)
    if debug:
        DEBUG_DIR.mkdir(exist_ok=True)
        (DEBUG_DIR / f"page{n}.html").write_text(html, encoding="utf-8")
    name, days, slots = parse(html)
    name = name or f"booking page {n}"

    state = load_state()
    state.pop("notified", None)  # from the old "next 48 hours" mode
    snapshots = state.setdefault("snapshots", {})
    alerts = state.setdefault("alerts", {})
    prev = snapshots.get(url)
    cancellations = find_cancellations(prev, slots, now) if prev and days else []
    if window_hours is not None:
        cancellations = [s for s in cancellations if s <= now + timedelta(hours=window_hours)]

    print(f"[{now:%Y-%m-%d %H:%M}] {name}: {len(slots)} slots over {len(days)} days"
          f"{f' ({days[0]} to {days[-1]})' if days else ''}; "
          + (f"cancellations: {len(cancellations)}" if prev else "first check, snapshot saved"))
    if slots:
        print(f"  earliest: {slots[0]:%a %d %b %H:%M}")
    for s in cancellations:
        print(f"  * {s:%a %d %b %H:%M}")

    if cancellations and notifier.channels:
        shown = cancellations[:10]
        lines = "\n".join(f"{s:%a %d %b at %H:%M}" for s in shown)
        if len(cancellations) > len(shown):
            lines += f"\n…and {len(cancellations) - len(shown)} more"
        notifier.send(f"Cancellation{'s' if len(cancellations) > 1 else ''} at {name}",
                      f"{lines}\n\nEarliest free slot: {slots[0]:%a %d %b at %H:%M}\nTap to book.",
                      click=page_url, priority="urgent")
        print(f"  notified about {len(cancellations)} cancellation(s)")

    # Only replace the snapshot with a page we could read, so a blip that returns an
    # empty page doesn't make every slot look like a cancellation next time.
    if days:
        snapshots[url] = snapshot(slots, days)

    # No day blocks at all means the page layout changed (or the link expired).
    # Alert at most once a day so a broken watcher doesn't fail silently.
    if not days and notifier.channels:
        last = alerts.get(url, "")
        if last < (now - timedelta(days=1)).isoformat():
            notifier.send("Blood test watcher needs attention",
                          f"Couldn't find any appointment days on {name}. The link may have "
                          "expired or the page layout changed. Run: python watcher.py --once --debug",
                          click=url, priority="default")
            alerts[url] = now.isoformat()

    save_state(state)
    return len(cancellations)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", action="append", help="booking page URL (repeatable; default: urls.txt)")
    ap.add_argument("--topic", default=os.environ.get("NTFY_TOPIC"), help="ntfy topic")
    ap.add_argument("--whatsapp-phone", default=os.environ.get("WHATSAPP_PHONE"), help="e.g. +447700900123")
    ap.add_argument("--whatsapp-apikey", default=os.environ.get("WHATSAPP_APIKEY"), help="CallMeBot API key")
    ap.add_argument("--hours", type=float, default=float(os.environ["WINDOW_HOURS"]) if os.environ.get("WINDOW_HOURS") else None,
                    help="only report cancellations starting within this many hours (default: any)")
    ap.add_argument("--interval", type=int, default=300, help="seconds between checks in loop mode")
    ap.add_argument("--once", action="store_true", help="check once and exit")
    ap.add_argument("--debug", action="store_true", help="save the fetched page to debug/page.html")
    ap.add_argument("--test-notify", action="store_true", help="send a test notification and exit")
    args = ap.parse_args()

    notifier = Notifier(args.topic, args.whatsapp_phone, args.whatsapp_apikey)
    if args.whatsapp_phone and not args.whatsapp_apikey or args.whatsapp_apikey and not args.whatsapp_phone:
        print("WARNING: WhatsApp needs both WHATSAPP_PHONE and WHATSAPP_APIKEY; skipping it.", file=sys.stderr)

    if args.test_notify:
        if not notifier.channels:
            sys.exit("No notification channels: set NTFY_TOPIC and/or WHATSAPP_PHONE + WHATSAPP_APIKEY")
        notifier.send("Blood test watcher", "Test notification: it works!")
        print(f"Sent via {', '.join(notifier.channels)}.")
        return
    urls = load_urls(args.url)
    if not urls:
        sys.exit("No booking URLs: add them to urls.txt, set BOOKING_URLS, or pass --url")
    if not notifier.channels:
        print("WARNING: no notification channels set; will only print results.", file=sys.stderr)

    while True:
        failed = 0
        for n, url in enumerate(urls, 1):
            try:
                check(url, notifier, args.hours, args.debug, n)
            except Exception as e:  # one bad page or network blip shouldn't stop the others
                failed += 1
                print(f"check failed for {url}: {e!r}", file=sys.stderr)
        if args.once:
            sys.exit(1 if failed else 0)
        time.sleep(args.interval + random.randint(0, 60))


if __name__ == "__main__":
    main()
