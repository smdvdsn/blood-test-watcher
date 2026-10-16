"""Exercises the DOM extraction + parsing against a few plausible page layouts.
Run: python -m pytest tests/  (uses a real headless Chromium via Playwright)."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import watcher  # noqa: E402

NOW = datetime(2026, 10, 16, 13, 0, tzinfo=watcher.TZ)

LAYOUTS = {
    # Date headings with time buttons underneath
    "headings": """
      <p>Opening hours 08:00 - 16:00</p>
      <h3>Friday 17th October 2026</h3>
      <button class="slot">09:10</button><button class="slot disabled">09:20</button>
      <h3>Monday 20th October 2026</h3>
      <button class="slot">11:00</button>""",
    # Dates carried in attributes
    "attrs": """
      <div data-date="2026-10-17"><a href="#" onclick="pick()">2:30pm</a></div>
      <div data-date="2026-10-25"><a href="#">10:00</a></div>""",
    # Table of radio buttons
    "radios": """
      <table><tr><th>17/10/2026</th></tr>
      <tr><td><label><input type="radio" name="t" value="1">08:40</label></td></tr></table>""",
    "none": "<p>Sorry, there are no available appointments at this clinic.</p>",
}

def run(html, tmp_path):
    f = tmp_path / "page.html"
    f.write_text(f"<html><body>{html}</body></html>")
    raw = watcher.fetch(f.as_uri(), debug=False)
    slots, undated = watcher.interpret(raw, NOW)
    return slots, undated, raw

def dt(*a):
    return datetime(*a, tzinfo=watcher.TZ)

def test_headings(tmp_path):
    slots, _, _ = run(LAYOUTS["headings"], tmp_path)
    assert slots == [dt(2026, 10, 17, 9, 10), dt(2026, 10, 20, 11, 0)]

def test_attrs(tmp_path):
    slots, _, _ = run(LAYOUTS["attrs"], tmp_path)
    assert slots == [dt(2026, 10, 17, 14, 30), dt(2026, 10, 25, 10, 0)]

def test_radios(tmp_path):
    slots, _, _ = run(LAYOUTS["radios"], tmp_path)
    assert slots == [dt(2026, 10, 17, 8, 40)]

def test_none(tmp_path):
    slots, undated, raw = run(LAYOUTS["none"], tmp_path)
    assert not slots and not undated and watcher.NO_SLOTS_RE.search(raw["bodyText"])

def test_window():
    slots = [dt(2026, 10, 17, 9, 10), dt(2026, 10, 20, 11, 0)]
    assert [s for s in slots if NOW <= s <= NOW + timedelta(hours=48)] == [slots[0]]

def test_parse_date_rollover():
    assert watcher.parse_date("3 Jan", datetime(2026, 12, 28).date()).year == 2027
