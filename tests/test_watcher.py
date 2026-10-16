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


def page(days):
    """Timescreen markup for {"YYYY-MM-DD": ["HH:MM", ...]}."""
    out = ['<div id="timescreen-header"><h3> Farnham Hospital Blood Tests </h3></div>']
    for d, times in days.items():
        out.append(f'<div class="timescreen-day" data-date="{d}"><ul class="time-container">')
        out += [f'<li data-time="{t}" class="timescreen-time">{t}</li>' for t in times]
        out.append("</ul></div>")
    return "\n".join(out)


class Harness:
    """Runs check() against a sequence of fake pages, capturing notifications."""

    def __init__(self, monkeypatch, tmp_path, now=(2026, 10, 16, 13, 0)):
        monkeypatch.setattr(watcher, "STATE_FILE", tmp_path / "state.json")
        self.html = ""
        monkeypatch.setattr(watcher, "fetch", lambda url: self.html)
        self.sent = []
        monkeypatch.setattr(watcher, "notify", lambda *a, **k: self.sent.append(a))

        class FixedNow(datetime):
            @classmethod
            def now(cls, tz=None):
                return dt(*now)

        monkeypatch.setattr(watcher, "datetime", FixedNow)

    def run(self, days, url="u", hours=None):
        self.html = page(days) if isinstance(days, dict) else days
        return watcher.check(url, watcher.Notifier("topic"), hours, debug=False)


BASE = {"2026-10-20": ["09:00"], "2026-10-21": ["10:00", "10:05"], "2026-10-22": ["11:00"]}


def test_first_check_only_records(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    assert h.run(BASE) == 0 and h.sent == []


def test_cancellation_detected_once(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    h.run(BASE)
    # Someone cancels 17 Oct 08:30 (before the old earliest day) and 21 Oct 10:10.
    later = {"2026-10-17": ["08:30"], **BASE, "2026-10-21": ["10:00", "10:05", "10:10"]}
    assert h.run(later) == 2
    assert len(h.sent) == 1
    topic, title, message = h.sent[0][:3]
    assert title == "Cancellations at Farnham Hospital Blood Tests"
    assert "Sat 17 Oct at 08:30\nWed 21 Oct at 10:10" in message
    assert "Earliest free slot: Sat 17 Oct at 08:30" in message
    # Unchanged on the next check: no repeat.
    assert h.run(later) == 0 and len(h.sent) == 1


def test_booked_slots_and_new_days_are_not_cancellations(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    h.run(BASE)
    # 20 Oct 09:00 got booked; a new day (23 Oct) was released at the end of the window.
    assert h.run({"2026-10-21": ["10:00", "10:05"], "2026-10-22": ["11:00"], "2026-10-23": ["09:00"]}) == 0
    assert h.sent == []
    # A booked slot freeing up again is a cancellation.
    assert h.run(BASE) == 1 and "Tue 20 Oct at 09:00" in h.sent[0][2]


def test_empty_page_does_not_reset_snapshot(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    h.run(BASE)
    h.run("<p>Service temporarily unavailable</p>")
    assert [title for _, title, *_ in h.sent] == ["Blood test watcher needs attention"]
    assert h.run(BASE) == 0  # nothing looks new once the page is back


def test_pages_tracked_separately(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    h.run(BASE, url="a")
    h.run({"2026-10-20": []}, url="b")
    # Only 20 Oct 09:00 is a cancellation for b; 21-22 Oct are beyond b's previous last day.
    assert h.run(BASE, url="b") == 1
    assert h.run(BASE, url="a") == 0  # a already had all of these


def test_window_hours(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    h.run(BASE)
    later = {**BASE, "2026-10-17": ["08:30"], "2026-10-22": ["11:00", "15:00"]}
    assert h.run(later, hours=48) == 1  # 17 Oct counts, 22 Oct is too far ahead


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
    watcher.check("https://x.test/pre_timescreen.php?id=$2y$10$abc.", watcher.Notifier(), 24 * 365 * 5, debug=False)
    assert fetched == ["https://x.test/pre_timescreen.php?id=$2y$10$abc.", "https://x.test/timescreen.php?id=$2y$10$abc.&rid=52"]


class FakeResponse:
    def __init__(self, body):
        self.body = body.encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def test_whatsapp_request(monkeypatch):
    from urllib.parse import parse_qs, urlparse
    calls = []
    monkeypatch.setattr(watcher.urllib.request, "urlopen",
                        lambda req, timeout: calls.append(req.full_url) or FakeResponse("Message queued."))
    watcher.notify_whatsapp("+447700900123", "123456", "Slot at Farnham", "Fri 17 Oct at 09:10",
                            click="https://www.swiftqueue.co.uk/timescreen.php?id=$2y$10$a/b.&rid=52")
    q = parse_qs(urlparse(calls[0]).query)
    assert q["phone"] == ["+447700900123"] and q["apikey"] == ["123456"]
    assert q["text"] == ["*Slot at Farnham*\nFri 17 Oct at 09:10\n"
                         "https://www.swiftqueue.co.uk/timescreen.php?id=%242y%2410%24a/b.&rid=52"]


def test_whatsapp_bad_key(monkeypatch):
    monkeypatch.setattr(watcher.urllib.request, "urlopen", lambda req, timeout: FakeResponse("<p>APIKey is invalid.</p>"))
    try:
        watcher.notify_whatsapp("+447700900123", "bad", "t", "m")
    except RuntimeError as e:
        assert "APIKey is invalid" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


def test_notifier_partial_failure(monkeypatch):
    sent = []
    monkeypatch.setattr(watcher, "notify", lambda *a: sent.append("ntfy"))
    def boom(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(watcher, "notify_whatsapp", boom)
    n = watcher.Notifier("topic", "+447700900123", "key")
    assert n.channels == ["ntfy", "WhatsApp"]
    n.send("t", "m")  # WhatsApp down but ntfy worked: no exception
    assert sent == ["ntfy"]
    monkeypatch.setattr(watcher, "notify", boom)
    try:
        n.send("t", "m")
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError when every channel fails")


def test_load_dotenv(monkeypatch, tmp_path):
    f = tmp_path / ".env"
    f.write_text("# comment\nNTFY_TOPIC=bt-abc\nexport WHATSAPP_PHONE='+447700900123'\n"
                 'WHATSAPP_APIKEY="12 34"\nEMPTY=\nWINDOW_HOURS=6\n')
    for k in ("NTFY_TOPIC", "WHATSAPP_PHONE", "WHATSAPP_APIKEY", "EMPTY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("WINDOW_HOURS", "48")  # already set: .env must not override it
    watcher.load_dotenv(f)
    import os
    assert os.environ["NTFY_TOPIC"] == "bt-abc"
    assert os.environ["WHATSAPP_PHONE"] == "+447700900123"
    assert os.environ["WHATSAPP_APIKEY"] == "12 34"
    assert os.environ["EMPTY"] == ""
    assert os.environ["WINDOW_HOURS"] == "48"
