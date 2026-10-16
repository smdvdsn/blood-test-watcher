# Blood test slot watcher

Checks a Swiftqueue booking page (`timescreen.php`) on a schedule. When an appointment
is available within the next 48 hours (UK time), it sends a push notification to
your phone. Tapping the notification opens the booking page.

## How it works

1. Downloads the booking page. Swiftqueue puts every bookable slot for the next few
   weeks straight into the HTML, so no browser is needed. Each day is a
   `div.timescreen-day[data-date]` and each free slot a `li.timescreen-time[data-time]`.
   Booked times aren't listed.
2. Sends a notification via [ntfy](https://ntfy.sh) for any slot starting in the
   next 48 hours. Each slot is notified only once.
3. Sends a once-a-day "needs attention" alert if the page has no appointment days at
   all. That usually means the link expired or the page layout changed.

Only Python 3.9+ is needed; there are no packages to install. On Windows, also run
`pip install tzdata` so UK time zone data is available.

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
topic stay hidden in secrets). Public repos get unlimited Actions minutes. Each run is
billed as at least 1 minute, so on a private repo (2,000 free minutes a month) change
the cron to `*/30 * * * *`.

## 2b. Or run it on your own computer

```bash
export BOOKING_URL='https://www.swiftqueue.co.uk/timescreen.php?id=...&rid=52'   # keep the single quotes ($ signs!)
export NTFY_TOPIC='bloodtest-k3v9q2x7m'

python watcher.py --test-notify      # should buzz your phone
python watcher.py --once            # one check; prints slot count and earliest slot
python watcher.py                    # keep checking every ~5 minutes
```

## Options

| Flag / env var | Default | Meaning |
|---|---|---|
| `--hours` / `WINDOW_HOURS` | 48 | How far ahead counts as "soon" |
| `--interval` | 300 | Seconds between checks in loop mode (plus up to 60s of jitter) |
| `--debug` | off | Saves the fetched page to `debug/page.html` |
| `NTFY_SERVER` | `https://ntfy.sh` | If you self-host ntfy |

## If it stops working

You'll get a "needs attention" notification. Run `python watcher.py --once --debug`
and open `debug/page.html`. If the page now says the link has expired, get a fresh
link from Swiftqueue and update `BOOKING_URL`. If Swiftqueue changed its markup,
update `TimescreenParser` in `watcher.py`.

Please keep the check interval reasonable (5–10 minutes). It's an NHS booking system.

## Tests

```bash
pip install pytest && python -m pytest tests/
```
