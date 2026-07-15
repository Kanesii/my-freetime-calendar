# Personal Time Auto-Scheduler

Reads your Push work schedule (ICS feed), finds open gaps in your day, and
publishes a "Personal Time" calendar feed of your own — 6 hours a week by
default, spread across whatever free time exists — that you can subscribe to
in Apple Calendar just like the Push feed. It re-runs automatically every 6
hours on GitHub's free tier, so it stays current with your shifts.

## One-time setup (about 10 minutes)

### 1. Create a GitHub account (if you don't have one)
Free at github.com.

### 2. Create a new repository
- Click "New repository"
- Name it something like `my-freetime-calendar`
- Set it to **Public** (raw files from private repos require extra auth
  headers that Apple Calendar can't send — public is simplest and the repo
  only contains times, no personal details, unless you put your work
  location in event titles)
- Click "Create repository"

### 3. Upload these three files, keeping the folder structure
```
.github/workflows/update-schedule.yml
schedule_freetime.py
README.md   (optional, just for your own reference)
```
On GitHub: "Add file" > "Upload files", drag them in. Make sure
`update-schedule.yml` ends up inside `.github/workflows/` — GitHub usually
preserves the folder structure automatically when you drag the whole folder.

### 4. Add your Push link as a secret (keeps it private)
- In your repo: Settings > Secrets and variables > Actions > New repository secret
- Name: `PUSH_ICS_URL`
- Value: paste the link Push gave you
- Save

### 5. Turn on Actions and run it once
- Go to the "Actions" tab of your repo
- If prompted, click "I understand my workflows, go ahead and enable them"
- Click "Update Personal Time Calendar" on the left, then "Run workflow" (top right) to trigger it manually the first time
- Wait ~30 seconds, refresh — you should see a green checkmark and a new
  `personal_time.ics` file appear in your repo's file list

### 6. Subscribe in Apple Calendar
- Get the raw file URL: click on `personal_time.ics` in your repo, click
  "Raw", and copy that URL. It'll look like:
  `https://raw.githubusercontent.com/YOUR-USERNAME/my-freetime-calendar/main/personal_time.ics`
- On iPhone: Settings > Calendar > Accounts > Add Account > Other >
  Add Subscribed Calendar > paste that URL
- On Mac: Calendar app > File > New Calendar Subscription > paste that URL

From here it updates itself every 6 hours, same as Push.

## Adjusting your settings
Open `.github/workflows/update-schedule.yml` and edit the values under
`env:` — for example, change `WEEKLY_HOURS_TARGET` to `8`, or `WAKE_START`
to `"06:00"`. Commit the change and the next run will use it. No need to
touch the Python file itself unless you want to change the underlying logic.

Settings you can tweak:
- `WEEKLY_HOURS_TARGET` — total hours per week to schedule (default 6)
- `WAKE_START` / `WAKE_END` — the window personal time can fall in (default 7am-11pm)
- `MIN_BLOCK_MINUTES` — smallest gap worth using (default 30 min)
- `MAX_BLOCK_MINUTES` — largest single block it'll create (default 120 min)
- `LOOKAHEAD_DAYS` — how far ahead to plan (default 21 days / 3 weeks)
- `TIMEZONE` — set to `"America/New_York"` if you're currently in NYC, `"America/Chicago"` for Milwaukee
- `EVENT_TITLE` — what the blocks are called on your calendar

## How the allocation works
For each week in the lookahead window, it looks at every gap between your
shifts (within the wake window), then fills those gaps chronologically
until it hits your weekly hour target, capping any single block at
`MAX_BLOCK_MINUTES`. It re-generates the whole feed fresh each run using
stable event IDs, so re-running doesn't create duplicates — it just adjusts
to your latest shifts.

## Privacy note
The repo is public, but the only thing in it is generated free/busy times
under a generic "Personal Time" label — no shift details, no employer name.
Your actual Push link is stored as an encrypted GitHub secret and is never
exposed in the repo or in the published calendar.
