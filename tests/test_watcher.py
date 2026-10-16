"""Run: python -m pytest tests/"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import watcher  # noqa: E402

# Trimmed copy of the real Swiftqueue timescreen.php markup (Oct 2026).
PAGE = """
<style>li.timescreen-time:focus { outline: 2px solid green; }</style>
<div id="timescreen-header" class="row">
  <div class="col-md-12"><div class="col-md-10">
    <h3><span class="fa fa-hospital-o"></span> Farnham Hospital Blood Tests </h3>
    <h4><span class="fa fa-map-marker"></span> Hale Rd, Farnham GU9 9QL</h4>
</div></div></div>
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
    name, days, slots = watcher.parse(PAGE)
    assert name == "Farnham Hospital Blood Tests"
    assert days == ["2026-10-17", "2026-10-20"]
    assert slots == [dt(2026, 10, 17, 9, 10), dt(2026, 10, 17, 9, 15), dt(2026, 10, 20, 14, 30)]


def test_parse_no_days():
    assert watcher.parse("<p>Something else entirely</p>") == ("", [], [])


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
    assert sent[0][1] == "Blood test slots at Farnham Hospital Blood Tests"
    # Second run: same slots, no repeat notification.
    watcher.check("u", "topic", 48, debug=False)
    assert len(sent) == 1
    # Same times on a different booking page are still new.
    watcher.check("u2", "topic", 48, debug=False)
    assert len(sent) == 2


def test_load_urls(monkeypatch, tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text("# comment\nhttps://a?id=$2y$10$x&rid=1\n\n  https://b  \n")
    monkeypatch.setattr(watcher, "URLS_FILE", f)
    monkeypatch.delenv("BOOKING_URLS", raising=False)
    assert watcher.load_urls(None) == ["https://a?id=$2y$10$x&rid=1", "https://b"]
    monkeypatch.setenv("BOOKING_URLS", "https://c\nhttps://d")
    assert watcher.load_urls(None) == ["https://c", "https://d"]
    assert watcher.load_urls(["https://e"]) == ["https://e"]


# Trimmed copy of a real pre_timescreen.php "appointment reason" page.
PRE_PAGE = """
<div id="timescreen-header" class="row"><div class="col-md-12"><div class="col-md-10">
  <h2><span class="fa fa-hospital-o"></span> Frimley Blood Tests (Patient bookable) CLINIC A </h2>
  <h3 class="facility-subheading"><span class="fa fa-map-marker"></span> Frimley Hospital</h3>
</div></div></div>
<h3>Please Select Your Appointment Reason</h3>
<a class="btn btn-lg rounded sq-green btn-block" href="timescreen.php?id=$2y$10$abc.&rid=52">
  <h4><span class="fa fa-clock-o"></span> Blood Test <span class="fa fa-chevron-right pull-right"></h4>
</a>
<script>var t = "timescreen.php?id=10850&rid=1144";</script>
"""


def test_pre_timescreen_name_and_link():
    assert watcher.parse(PRE_PAGE)[0] == "Frimley Blood Tests (Patient bookable) CLINIC A"
    url = "https://www.swiftqueue.co.uk/pre_timescreen.php?id=$2y$10$abc."
    assert watcher.blood_test_link(PRE_PAGE, url) == "https://www.swiftqueue.co.uk/timescreen.php?id=$2y$10$abc.&rid=52"


def test_check_follows_pre_timescreen(monkeypatch, tmp_path):
    monkeypatch.setattr(watcher, "STATE_FILE", tmp_path / "state.json")
    fetched = []
    monkeypatch.setattr(watcher, "fetch", lambda u: fetched.append(u) or (PRE_PAGE if "pre_" in u else PAGE))
    watcher.check("https://x.test/pre_timescreen.php?id=$2y$10$abc.", None, 24 * 365 * 5, debug=False)
    assert fetched == ["https://x.test/pre_timescreen.php?id=$2y$10$abc.", "https://x.test/timescreen.php?id=$2y$10$abc.&rid=52"]
