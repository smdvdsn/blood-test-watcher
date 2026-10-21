# Blood test cancellation watcher

Checks one or more Swiftqueue booking pages (`timescreen.php`) on a schedule. When a
**cancellation** frees up a slot, it sends a push notification and/or WhatsApp
message to your phone naming the clinic, the freed slot(s) and the earliest free
slot. Tapping the notification opens that clinic's booking page.

The pages to watch are listed in [`urls.txt`](urls.txt), one per line. Add or remove
lines to change which clinics are watched.
Both kinds of Swiftqueue link work: a `timescreen.php` link (the "choose your time"
page) or a `pre_timescreen.php` link (the "choose appointment reason" page). For the
second kind, the watcher follows the Blood Test option itself.

## How it works

1. Downloads each booking page. Swiftqueue puts every bookable slot for the next few
   weeks straight into the HTML, so no browser is needed. Each day is a
   `div.timescreen-day[data-date]` and each free slot a `li.timescreen-time[data-time]`.
   Booked times aren't listed.
2. Compares the free slots with the previous check (saved in `state.json`). A slot
   that is free now but wasn't before is a **cancellation**. Two cases are left out:
   - **Slots that get booked** (disappear). Nothing to report.
   - **Newly released days.** Each clinic opens up new days at the end of its booking
     window. New slots dated after the last day the previous check could see are
     ignored.
3. Sends an alert via [ntfy](https://ntfy.sh) and/or WhatsApp for each check that
   finds cancellations. The first check of a page only records its slots.
4. Sends a once-a-day "needs attention" alert for any page with no appointment days
   at all. That usually means the link expired or the page layout changed.

Only Python 3.9+ is needed; there are no packages to install. On Windows, also run
`pip install tzdata` so UK time zone data is available.

## 1. Set up your phone

Use either or both of these. If both are set up, every alert goes to both, and one
failing doesn't stop the other.

### Option A: ntfy push notifications (2 minutes, no account needed)

1. Install **ntfy** ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) / [iPhone](https://apps.apple.com/app/ntfy/id1625396347)).
2. Tap **+** and subscribe to a topic with a long, hard-to-guess name, e.g. `bloodtest-k3v9q2x7m`.
   Anyone who knows the topic name can read it, so don't use something obvious.

### Option B: WhatsApp messages (via CallMeBot)

WhatsApp has no free official API for messaging yourself, so this uses
[CallMeBot](https://www.callmebot.com/blog/free-api-whatsapp-messages/), a free
service for personal use.

1. Follow the steps on that page: add the CallMeBot number it shows to your contacts,
   then send it the activation message it gives you over WhatsApp. The number
   changes from time to time, so always take it from that page.
2. Within a couple of minutes you'll get a reply with your **API key**. If you don't,
   CallMeBot says to try again after 24 hours.
3. Your settings are `WHATSAPP_PHONE` (your own number in international format, e.g.
   `+447700900123`) and `WHATSAPP_APIKEY` (the key from step 2).

Caveats: CallMeBot is a small third-party service with no delivery guarantee, and
it may be slow or down at times. Your API key only sends messages to your own number.
Using ntfy as well gives you a backup.

## 2. Run it on your own computer (recommended)

Any computer that's switched on most of the time will do: a laptop, desktop PC, Mac
or Raspberry Pi. Requests come from your home internet connection, so this keeps
working when Swiftqueue blocks cloud servers (see the note under step 3).

### Get the code and your settings

1. Install Python 3.9 or newer from [python.org](https://www.python.org/downloads/)
   if you don't have it (on Windows, tick **Add python.exe to PATH**). Then
   download this repo: **Code → Download ZIP** on GitHub and unzip it, or
   `git clone` it. On a Mac, don't put it in Desktop, Documents or Downloads (see
   "Run it automatically" below for why).
2. Open a terminal in that folder (Windows: **Command Prompt**; Mac: **Terminal**)
   and create your settings file:

   ```bash
   cp .env.example .env      # Windows: copy .env.example .env
   ```

   Open `.env` in a text editor and fill in `NTFY_TOPIC` and/or `WHATSAPP_PHONE` +
   `WHATSAPP_APIKEY`. `.env` is in `.gitignore`, so it's never committed.
   Variables already set in your shell take priority over `.env`.
3. On Windows, also run `pip install tzdata` (UK time zone data).

In the commands below, use `python3` instead of `python` on a Mac or Linux, and
`py` instead of `python` on Windows if `python` isn't found.

### Check that it works

```bash
python watcher.py --test-notify      # sends a test message on every channel you've set
python watcher.py --once             # checks every page in urls.txt once
```

The first check only records the current slots, so a normal result looks like
this, with no alert:

```
[2026-10-21 10:20] Farnham Hospital Blood Tests: 1234 slots over 25 days (2026-10-21 to 2026-12-23); first check, snapshot saved
```

### Keep it running

**Easiest: leave a window open.** This checks every ~5 minutes until you close the
window or the computer sleeps:

```bash
python watcher.py
```

**Better: run it automatically.** Schedule a single check (`--once`) every 10
minutes. It then carries on after restarts without a window open. The scheduled
command must start in the project folder, because `state.json` (the record of the
last check) is saved in the current folder.

<details>
<summary><b>Windows: Task Scheduler</b></summary>

1. In the project folder, create a file called `run-watcher.bat` containing
   (with your own folder path):

   ```bat
   cd /d "C:\Users\you\blood-test-watcher"
   python watcher.py --once >> watcher.log 2>&1
   ```

   Double-click it once, then check that `watcher.log` shows a successful check.
2. Open Command Prompt and run (with the full path to the `.bat` file):

   ```bat
   schtasks /Create /TN "Blood test watcher" /SC MINUTE /MO 10 /TR "C:\Users\you\blood-test-watcher\run-watcher.bat"
   ```

   A command window flashes up briefly every 10 minutes while you're logged in.
   To stop it, run `schtasks /Delete /TN "Blood test watcher"`, or delete it in
   **Task Scheduler**.

</details>

<details>
<summary><b>Mac, Linux or Raspberry Pi: cron</b></summary>

1. Find the full path to Python with `which python3`, and to the project folder
   with `pwd` (run inside it).
2. Run `crontab -e` and add this line, using your paths:

   ```
   */10 * * * * cd /Users/you/blood-test-watcher && /usr/bin/python3 watcher.py --once >> watcher.log 2>&1
   ```

3. After 10 minutes, check that `watcher.log` in the project folder shows a
   successful check. To stop, run `crontab -e` again and delete the line.

On a Mac, cron isn't allowed to read the Desktop, Documents or Downloads folders,
so keep the project somewhere else, e.g. directly in your home folder. If macOS
asks whether cron may access files, allow it.

</details>

Things to know when running it locally:

- **Sleep pauses it.** No checks run while the computer is asleep or switched
  off. On a laptop or desktop, set it to stay awake while plugged in (Windows:
  **Settings → System → Power**; Mac: **System Settings → Battery/Energy → Prevent
  automatic sleeping**). A Raspberry Pi never sleeps.
- `watcher.log` grows by a few lines per check. Delete it now and then if it gets
  big. It's in `.gitignore`.
- If a check fails, the log shows `check failed for ...` with the error. A run of
  `HTTP 403` errors means Swiftqueue is blocking your connection too.

## 3. Or run it in the cloud with GitHub Actions

> **Note (October 2026):** since 20 October 2026, Swiftqueue has answered every
> request from GitHub Actions servers with `HTTP 403 Forbidden`, so every run
> fails. The same requests from a home connection work. Use step 2 unless this
> changes. The workflow is kept here in case the block is lifted.

1. Push this folder to a new GitHub repo.
2. In the repo, go to **Settings → Secrets and variables → Actions** and add:
   - `NTFY_TOPIC`: your ntfy topic name (if using ntfy)
   - `WHATSAPP_PHONE` and `WHATSAPP_APIKEY` (if using WhatsApp)
   - Optional: `BOOKING_URLS`, one URL per line. Use this instead of `urls.txt` if you'd
     rather not have the links visible in a public repo.
3. Go to **Actions → Watch for blood test slots → Run workflow** and check that the log looks sane.

It then runs every ~15 minutes. **Make the repo public** if you can (your topic and
WhatsApp details stay hidden in secrets). Public repos get unlimited Actions minutes. Each run is
billed as at least 1 minute, so on a private repo (2,000 free minutes a month) change
the cron to `*/30 * * * *`.

To stop the scheduled runs, go to **Actions → Watch for blood test slots → ⋯ →
Disable workflow**.

## Options

| Flag / env var | Default | Meaning |
|---|---|---|
| `--url` (repeatable) / `BOOKING_URLS` | `urls.txt` | Pages to watch; overrides `urls.txt` |
| `--hours` / `WINDOW_HOURS` | any | Only report cancellations starting within this many hours |
| `--interval` | 300 | Seconds between checks in loop mode (plus up to 60s of jitter) |
| `--debug` | off | Saves fetched pages to `debug/page1.html`, `page2.html`, … |
| `NTFY_SERVER` | `https://ntfy.sh` | If you self-host ntfy |

## If it stops working

You'll get a "needs attention" notification. Run `python watcher.py --once --debug`
and open the matching `debug/pageN.html`. If the page now says the link has expired,
get a fresh link from Swiftqueue and update `urls.txt`. If Swiftqueue changed its markup,
update `TimescreenParser` in `watcher.py`.

Please keep the check interval reasonable (5–10 minutes). It's an NHS booking system.

## Tests

```bash
pip install pytest && python -m pytest tests/
```

## Things to know

- A slot can appear briefly without being a real cancellation, for example if
  Swiftqueue holds slots while someone is part-way through booking and then
  releases them. You may occasionally get an alert for a slot that's gone again
  by the time you look.
- On GitHub Actions the snapshot is carried between runs in the Actions cache. If
  the cache is cleared or expires (7 days without a run), the next run just takes a
  fresh snapshot.
