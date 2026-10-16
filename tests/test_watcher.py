"""Run: python -m pytest tests/"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import watcher  # noqa: E402

# Trimmed copy of the real Swiftqueue timescreen.php markup (Oct 2026).
PAGE = """
<style>li.timescreen-time:focus { outline: 2px solid green; }</style>
<div class="timescreen-day" data-date="2026-10-17"><h3 class="date-title">Sat, 17 Oct</h3>
  <ul id="morning-times-1" class="time-container" style="display:none;">
    <li data-time="09:10" class="timescreen-time">09:10</li>
    <li data-time="09:15" class="timescreen-time">09:15</li>
  </ul>
  <ul id="noon-times-2026-10-17" class="time-container" style="display:none;"></ul>
</div>
<div class="timescreen-day" data-date="2026-10-20"><h3 class="date-title">Tue, 20 Oct</h3>
  <ul id="afternoon-times-2026-10-20" class="time-container" style="display:none;">
    <li data-time="14:30" class="timescreen-time">14:30</li>
  </ul>
</div>
<script>var t = {"no_appointments_found": "No Appointments Found"};</script>
"""


def dt(*a):
    return datetime(*a, tzinfo=watcher.TZ)


def test_parse():
    days, slots = watcher.parse(PAGE)
    assert days == ["2026-10-17", "2026-10-20"]
    assert slots == [dt(2026, 10, 17, 9, 10), dt(2026, 10, 17, 9, 15), dt(2026, 10, 20, 14, 30)]


def test_parse_no_days():
    assert watcher.parse("<p>Something else entirely</p>") == ([], [])


def test_window(monkeypatch, tmp_path):
    monkeypatch.setattr(watcher, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(watcher, "fetch", lambda url: PAGE)
    sent = []
    monkeypatch.setattr(watcher, "notify", lambda *a, **k: sent.append(a))

    class FixedNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return dt(2026, 10, 16, 13, 0)

    monkeypatch.setattr(watcher, "datetime", FixedNow)
    assert watcher.check("u", "topic", 48, debug=False) == 2  # the two 17 Oct slots, not 20 Oct
    assert len(sent) == 1 and "17 Oct at 09:10" in sent[0][2]
    # Second run: same slots, no repeat notification.
    watcher.check("u", "topic", 48, debug=False)
    assert len(sent) == 1
