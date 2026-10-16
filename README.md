# Blood test slot watcher

Checks a Swiftqueue booking page (`timescreen.php`) on a schedule. When an appointment
is available within the next 48 hours (UK time), it sends a push notification to
your phone. Tapping the notification opens the booking page.

## How it works

1. Loads the page in headless Chromium, so slots rendered by JavaScript are picked up.
2. Finds clickable elements whose text is a time (`09:10`, `2:30pm`, …), skipping disabled or hidden ones.
3. Works out each slot's date from the element's own attributes, or from the nearest
   date heading above it (`Friday 17th October 2026`, `17/10/2026`, `2026-10-17`, …).
4. Sends a notification via [ntfy](https://ntfy.sh) for any new slot in the window.
   Each slot is notified only once.
5. Sends a one-a-day "needs attention" alert if the page shows neither slots nor a
   "no appointments" message. That usually means the page layout changed.

## 1. Set up your phone (2 minutes, no account needed)

1. Install **ntfy** ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) / [iPhone](https://apps.apple.com/app/ntfy/id1625396347)).
2. Tap **+** and subscribe to a topic with a long, hard-to-guess name, e.g. `bloodtest-k3v9q2x7m`.
   Anyone who knows the topic name can read it, so don't use something obvious.

## 2a. Run it in the cloud with GitHub Actions (recommended, nothing to leave running)

1. Push this folder to a new GitHub repo.
2. In the repo, go to **Settings → Secrets and variables → Actions** and add:
   - `BOOKING_URL`: your full `https://www.swiftqueue.co.uk/timescreen.php?id=...&rid=52` link
   - `NTFY_TOPIC`: your topic name
3. Go to **Actions → Watch for blood test slots → Run workflow** and check that the log looks sane.

It then runs every ~10 minutes. **Make the repo public** if you can (your URL and
topic stay hidden in secrets). Public repos get unlimited Actions minutes. A private
repo's 2,000 free minutes a month will run out at this frequency.

## 2b. Or run it on your own computer

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium

export BOOKING_URL='https://www.swiftqueue.co.uk/timescreen.php?id=...&rid=52'   # keep the single quotes ($ signs!)
export NTFY_TOPIC='bloodtest-k3v9q2x7m'

python watcher.py --test-notify      # should buzz your phone
python watcher.py --once --debug     # one check; look at debug/page.png
python watcher.py                    # keep checking every ~5 minutes
```

## Options

| Flag / env var | Default | Meaning |
|---|---|---|
| `--hours` / `WINDOW_HOURS` | 48 | How far ahead counts as "soon" |
| `--interval` | 300 | Seconds between checks in loop mode (plus up to 60s of jitter) |
| `--debug` | off | Saves `debug/page.html`, `page.png` and `candidates.json` |
| `NTFY_SERVER` | `https://ntfy.sh` | If you self-host ntfy |

## If it isn't finding slots

Run `python watcher.py --once --debug` and compare `debug/page.png` with the
printed slot list. `debug/candidates.json` lists every time-like element it saw,
with the date context it found for each. Common fixes:

- **The page only shows one day at a time, with a "next" button.** Add clicks in
  `fetch()` (e.g. `page.click("text=Next")`) and evaluate `EXTRACT_JS` after each one.
- **The page needs a postcode or other form step first.** Add the `page.fill(...)` /
  `page.click(...)` steps in `fetch()` before extraction.

Please keep the check interval reasonable (5–10 minutes). It's an NHS booking system.

## Tests

```bash
pip install pytest && python -m pytest tests/
```
