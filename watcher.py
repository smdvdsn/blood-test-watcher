#!/usr/bin/env python3
"""Watch a Swiftqueue booking page and push a phone notification (via ntfy)
when an appointment slot is available within the next N hours.

Usage:
    python watcher.py --once              # single check (cron / GitHub Actions)
    python watcher.py                     # loop forever, checking every --interval seconds
    python watcher.py --once --debug      # also save page.html / page.png / candidates.json
    python watcher.py --test-notify       # send a test push to your phone and exit

Configuration comes from environment variables (or CLI flags):
    BOOKING_URL   the Swiftqueue timescreen.php URL
    NTFY_TOPIC    your ntfy topic name (subscribe to it in the ntfy phone app)
    NTFY_SERVER   optional, defaults to https://ntfy.sh
    WINDOW_HOURS  optional, defaults to 48
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
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/London")
STATE_FILE = Path(os.environ.get("STATE_FILE", "state.json"))
DEBUG_DIR = Path("debug")

MONTHS = {
    m: i + 1
    for i, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
            ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
            ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
            ("dec", "december"),
        ]
    )
    for m in names
}
MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))

# Date patterns, tried in order. UK ordering (day before month) for numeric dates.
DATE_PATTERNS = [
    # 2026-10-17
    (re.compile(r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b"), lambda m: (int(m[1]), int(m[2]), int(m[3]))),
    # 17/10/2026, 17-10-2026, 17.10.2026
    (re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](20\d\d)\b"), lambda m: (int(m[3]), int(m[2]), int(m[1]))),
    # 17th October 2026, 17 Oct, Friday 17 October
    (
        re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({MONTH_ALT})\.?,?(?:\s+(20\d\d))?\b", re.I),
        lambda m: (int(m[3]) if m[3] else None, MONTHS[m[2].lower()], int(m[1])),
    ),
    # October 17th 2026, Oct 17
    (
        re.compile(rf"\b({MONTH_ALT})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?(?:\s+(20\d\d))?\b", re.I),
        lambda m: (int(m[3]) if m[3] else None, MONTHS[m[1].lower()], int(m[2])),
    ),
]
TIME_RE = re.compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\s*([ap]\.?m\.?)?(?![\d/.-]*\d)", re.I)
NO_SLOTS_RE = re.compile(
    r"no\s+(?:available\s+)?(?:appointments?|slots?|times?)|fully\s+booked|no\s+availability", re.I
)

# Runs inside the browser. Walks the DOM in document order, remembering the most
# recent date-looking text, and records every clickable-looking element whose text
# (or value) is a time of day, together with that date context and its attributes.
EXTRACT_JS = r"""
({timeSrc, dateSrc}) => {
  const timeRe = new RegExp(timeSrc, 'i');
  const dateRe = new RegExp(dateSrc, 'i');
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE']);
  const ATTRS = ['data-date', 'data-day', 'data-time', 'data-slot', 'data-value', 'value',
                 'href', 'onclick', 'title', 'aria-label', 'id', 'name', 'for', 'datetime'];

  const visible = el => {
    if (el.tagName === 'OPTION') return true;
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && el.getClientRects().length > 0;
  };
  const clickable = el => {
    for (let e = el, i = 0; e && i < 4; e = e.parentElement, i++) {
      if (['A', 'BUTTON', 'INPUT', 'OPTION', 'LABEL', 'SELECT'].includes(e.tagName)) return true;
      if (e.hasAttribute('onclick') || e.getAttribute('role') === 'button') return true;
      if (/slot|time|appoint|book|avail/i.test(e.className || '')) return true;
      if (getComputedStyle(e).cursor === 'pointer') return true;
    }
    return false;
  };
  const disabled = el => {
    for (let e = el, i = 0; e && i < 3; e = e.parentElement, i++) {
      if (e.disabled || e.getAttribute('aria-disabled') === 'true') return true;
      if (/\b(disabled|unavailable|booked|full|taken|inactive)\b/i.test(e.className || '')) return true;
    }
    return false;
  };
  const context = el => {
    const out = [];
    for (let e = el, i = 0; e && i < 6; e = e.parentElement, i++) {
      for (const a of ATTRS) { const v = e.getAttribute && e.getAttribute(a); if (v) out.push(v); }
    }
    return out;
  };

  const results = [];
  let lastDate = null;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
  for (let n = walker.currentNode; n; n = walker.nextNode()) {
    let text, el;
    if (n.nodeType === Node.TEXT_NODE) {
      el = n.parentElement;
      if (!el || SKIP.has(el.tagName)) continue;
      text = n.textContent.trim();
    } else if (n.tagName === 'INPUT' && ['button', 'submit', 'radio'].includes(n.type)) {
      el = n;
      text = (n.value || '').trim();
    } else {
      continue;
    }
    if (!text) continue;
    if (dateRe.test(text) && visible(el)) lastDate = text;
    if (!timeRe.test(text) || text.length > 60) continue;
    results.push({
      text,
      tag: el.tagName,
      cls: el.className || '',
      lastDate,
      attrs: context(el),
      visible: visible(el),
      clickable: clickable(el),
      disabled: disabled(el),
    });
  }
  return { results, bodyText: document.body.innerText, title: document.title };
}
"""


def parse_date(text: str, today: date) -> date | None:
    for pattern, fn in DATE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        year, month, day = fn(m)
        try:
            if year is None:
                d = date(today.year, month, day)
                if d < today - timedelta(days=7):  # e.g. "3 Jan" seen in late December
                    d = date(today.year + 1, month, day)
                return d
            return date(year, month, day)
        except ValueError:
            continue
    return None


def parse_time(text: str) -> tuple[int, int] | None:
    m = TIME_RE.search(text)
    if not m:
        return None
    hour, minute, ampm = int(m[1]), int(m[2]), (m[3] or "").lower().replace(".", "")
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    return hour, minute


def interpret(raw: dict, now: datetime) -> tuple[list[datetime], list[dict]]:
    """Turn raw DOM candidates into slot datetimes. Returns (dated_slots, undated_candidates)."""
    slots: set[datetime] = set()
    undated = []
    for c in raw["results"]:
        if not (c["visible"] and c["clickable"]) or c["disabled"]:
            continue
        hm = parse_time(c["text"])
        if not hm:
            continue
        # Prefer a date in the element's own text/attributes, then the nearest preceding date text.
        d = None
        for source in [c["text"], *c["attrs"], c["lastDate"] or ""]:
            d = parse_date(source, now.date())
            if d:
                break
        if d is None:
            undated.append(c)
            continue
        slots.add(datetime(d.year, d.month, d.day, hm[0], hm[1], tzinfo=TZ))
    return sorted(slots), undated


def fetch(url: str, debug: bool) -> dict:
    from playwright.sync_api import sync_playwright

    launch_args = {}
    if os.environ.get("CHROMIUM_PATH"):
        launch_args["executable_path"] = os.environ["CHROMIUM_PATH"]
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_args)
        page = browser.new_page(
            locale="en-GB",
            timezone_id="Europe/London",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
            ),
        )
        page.goto(url, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(2_000)  # let any late JS render the slot grid
        raw = page.evaluate(
            EXTRACT_JS,
            {"timeSrc": TIME_RE.pattern, "dateSrc": "|".join(p.pattern for p, _ in DATE_PATTERNS)},
        )
        if debug:
            DEBUG_DIR.mkdir(exist_ok=True)
            (DEBUG_DIR / "page.html").write_text(page.content(), encoding="utf-8")
            page.screenshot(path=str(DEBUG_DIR / "page.png"), full_page=True)
            (DEBUG_DIR / "candidates.json").write_text(json.dumps(raw["results"], indent=2))
        browser.close()
    return raw


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
    raw = fetch(url, debug)
    slots, undated = interpret(raw, now)
    soon = [s for s in slots if now <= s <= now + timedelta(hours=window_hours)]
    no_slots_msg = bool(NO_SLOTS_RE.search(raw["bodyText"]))

    print(f"[{now:%Y-%m-%d %H:%M}] page title: {raw['title']!r}")
    print(f"  slots found: {len(slots)}  within {window_hours:g}h: {len(soon)}  undated time elements: {len(undated)}"
          f"  'no appointments' text: {no_slots_msg}")
    for s in slots:
        print(f"   {'*' if s in soon else ' '} {s:%a %d %b %H:%M}")

    state = load_state()
    cutoff = (now - timedelta(days=3)).isoformat()
    state["notified"] = {k: v for k, v in state["notified"].items() if v > cutoff}

    new = [s for s in soon if s.isoformat() not in state["notified"]]
    if new and topic:
        lines = "\n".join(f"{s:%a %d %b at %H:%M}" for s in new)
        notify(topic, f"Blood test slot{'s' if len(new) > 1 else ''} available!",
               f"{lines}\n\nTap to book.", click=url, priority="urgent")
        for s in new:
            state["notified"][s.isoformat()] = now.isoformat()
        print(f"  notified about {len(new)} new slot(s)")

    # If the page shows nothing we can understand, the layout has probably changed.
    # Alert once a day so a broken scraper doesn't fail silently.
    if not slots and not no_slots_msg and topic:
        reason = "undated" if undated else "empty"
        last = state["alerts"].get(reason, "")
        if last < (now - timedelta(days=1)).isoformat():
            notify(topic, "Blood test watcher needs attention",
                   "Found time slots but couldn't tell their dates." if undated
                   else "Couldn't find any slots or a 'no appointments' message on the page. "
                        "Run with --debug and check debug/page.png.",
                   click=url, priority="default")
            state["alerts"][reason] = now.isoformat()

    save_state(state)
    return len(soon)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("BOOKING_URL"))
    ap.add_argument("--topic", default=os.environ.get("NTFY_TOPIC"))
    ap.add_argument("--hours", type=float, default=float(os.environ.get("WINDOW_HOURS", 48)))
    ap.add_argument("--interval", type=int, default=300, help="seconds between checks in loop mode")
    ap.add_argument("--once", action="store_true", help="check once and exit")
    ap.add_argument("--debug", action="store_true", help="save page.html, page.png, candidates.json to debug/")
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
