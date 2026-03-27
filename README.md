# Classroom YouTube Tracker

**What this does:** Takes a child's YouTube watch history (exported from their school Google account) and calculates how much time they spent on YouTube during school hours — and what they were watching.

**Who this is for:** Parent advocates and researchers. You do not need to be a programmer. These instructions assume you have [Claude](https://claude.ai) open in another tab to help you if anything goes wrong.


---

## What You Need Before Starting

- [ ] Your child's YouTube watch history file
- [ ] A free YouTube Data API key (one-time setup, takes ~10 minutes)
- [ ] Python installed on your computer
- [ ] About 30–60 minutes the first time

**If any of these steps confuse you:** Copy the error message or the step you're stuck on, paste it into Claude, and ask for help. That is exactly what Claude is for.

---

## Step 0 — Get Your Child's YouTube Watch History

You need one of these files from your child's school Google account:

| File | How to get it | Quality |
|------|--------------|---------|
| `watch-history.json` | Google Takeout → save to Google Drive | ✅ Best |
| `watch-history.html` | Google Takeout → default format | ✅ Good |
| YouTube history PDF | Print the YouTube History page to PDF | ⚠️ Backup only |

---

## Step 1 — Install Python

If you already have Python, skip this step.

1. Go to [python.org/downloads](https://www.python.org/downloads/)
2. Download the latest version (3.10 or higher)
3. Run the installer — on Windows, **check the box that says "Add Python to PATH"**
4. Open a Terminal (Mac) or Command Prompt (Windows) and type:
   ```
   python --version
   ```
   You should see something like `Python 3.12.1`. If you see an error, ask Claude for help.

---

## Step 2 — Download This Repository

**Option A — If you have git installed:**
```bash
git clone https://github.com/merrybouv/classroom-youtube-tracker.git
cd classroom-youtube-tracker
```

**Option B — Download as a zip:**
1. Click the green **Code** button at the top of this GitHub page
2. Click **Download ZIP**
3. Unzip it on your computer
4. Open Terminal/Command Prompt and navigate to the folder:
   ```
   cd path/to/classroom-youtube-tracker
   ```
   *(Ask Claude: "How do I navigate to a folder in Terminal on Mac/Windows?")*

---

## Step 3 — Install Required Libraries (One Time Only)

In your Terminal/Command Prompt, paste this exactly:

```bash
pip install -r requirements.txt
```

You should see a list of things being installed. Wait for it to finish.
If you see an error, copy it and ask Claude what to do.

---

## Step 4 — Get a Free YouTube API Key (One Time Only)

The analysis tool needs to look up each video's duration from YouTube. This requires a free API key.

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Sign in with any Google account (your personal one is fine — not the school account)
3. Click **Select a project** → **New Project** → name it anything → **Create**
4. In the search bar at the top, search for **YouTube Data API v3**
5. Click on it → click **Enable**
6. Click **Credentials** in the left menu → **Create Credentials** → **API Key**
7. Copy the key that appears (it looks like `AIzaSy...`)

**Save your API key somewhere safe.** You only need to do this once.

Now set it as an environment variable. In your Terminal:

**Mac/Linux:**
```bash
export YOUTUBE_API_KEY=AIzaSy...your_key_here...
```

**Windows (Command Prompt):**
```
set YOUTUBE_API_KEY=AIzaSy...your_key_here...
```

*(You'll need to do this each time you open a new Terminal window, unless you add it to your shell profile — ask Claude if you want to set it permanently.)*

---

## Step 5 — Fetch Video Metadata

This step looks up each video in the watch history and gets its duration, title, and channel. **It only needs to run once per dataset.** Results are saved so you never look up the same video twice.

```bash
python fetch_metadata.py --input watch-history.json
```

Replace `watch-history.json` with the path to your actual file. If the file is in a different folder:
```bash
python fetch_metadata.py --input /Users/yourname/Downloads/watch-history.json
```

**What this does:**
- Reads all the video IDs from your file
- Calls the YouTube API to get duration, title, channel for each one
- Saves results to `yt_metadata.csv` in the same folder
- If interrupted, it picks up where it left off

**What you'll see:** A progress counter like `Batch 1/38 (50 IDs)... 47/50 public`

**How long it takes:** About 1 minute for a typical dataset (~2,000 videos).

---

## Step 6 — Run the Analysis

```bash
python analyze.py \
  --input watch-history.json \
  --metadata yt_metadata.csv \
  --school-start 07:00 \
  --school-end 14:30 \
  --grade middle \
  --timezone central
```

**Adjust these settings for your child's school:**

| Option | What it means | Example |
|--------|--------------|---------|
| `--input` | Your watch history file | `watch-history.json` |
| `--metadata` | Output from Step 5 | `yt_metadata.csv` |
| `--school-start` | Time school starts | `08:00` |
| `--school-end` | Time school ends | `15:00` |
| `--grade` | Used for default times if you skip start/end | `elementary`, `middle`, or `high` |
| `--timezone` | Your school's timezone | `eastern`, `central`, `mountain`, `pacific`, `alaska`, `hawaii` |
| `--date-from` | Only look at dates after this | `2025-09-01` |
| `--date-to` | Only look at dates before this | `2026-01-31` |

**Windows users:** Replace the `\` line breaks with `^`:
```
python analyze.py ^
  --input watch-history.json ^
  --metadata yt_metadata.csv ^
  --school-start 07:00 ^
  --school-end 14:30 ^
  --grade middle
```

---

## Step 7 — Read Your Results

The tool creates three output files:

| File | What's in it |
|------|-------------|
| `yt_report.txt` | Summary report — start here |
| `yt_events.csv` | Every video open with elapsed time |
| `yt_daily.csv` | Per-day totals (opens, minutes, rapid browse) |

Open `yt_report.txt` in any text editor. It will show you:

```
── Video Opens ──────────────────────────────────────────
  Total opens              : 1,828
  School-hour opens        : 1,447  (79.2% of total)
  Avg opens per school day : 21.0

── Rapid Browse (elapsed < 60 sec) ──────────────────────
  Rapid-browse opens       : 1,175  (81.2% of school-hour opens)

── Estimated Watch Time ─────────────────────────────────
  Total elapsed hours      : 31.7
  Avg minutes per school day: 27.6
```

**What "rapid browse" means:** A video that was opened and then closed (or skipped) within 60 seconds. This includes YouTube Shorts (which are under 60 seconds by design) as well as longer videos the student clicked away from almost immediately. Both indicate the same thing: no sustained engagement.

**What "elapsed time" means:** A conservative estimate of how long each video was actually watched, calculated as the minimum of: (a) time until the next video was opened, (b) time remaining in the school day, and (c) the video's actual duration. This intentionally undercounts — it's a floor, not a ceiling.

---

## Troubleshooting

**"python: command not found"**
Try `python3` instead of `python`. Or ask Claude: "Python is installed but my terminal says command not found."

**"No module named pandas"**
Run `pip install -r requirements.txt` again. If that fails, ask Claude.

**"API key not valid"**
Make sure you set the environment variable in the same Terminal window where you're running the script. Check for typos.

**"YouTube API quota exceeded"**
You've hit the 10,000 daily limit. Wait until tomorrow and run again — it will pick up where it left off.

**PDF gives strange results**
PDF exports have minute-level timestamps only. Rapid browse counts are not reliable for PDF data. Use JSON or HTML when possible.

**Something else is wrong**
Copy the full error message from your Terminal and paste it to Claude with: *"I'm running the Classroom YouTube Tracker and got this error — what do I do?"*

---

## How the Analysis Works (Plain English)

Google Takeout only records *when* a video was opened — not how long it was watched. So we estimate watch time using this logic:

> **Estimated time on a video = the smallest of:**
> - How long until the next video was opened (gap to next)
> - How much time was left in the school day
> - How long the video actually is

This is conservative on purpose. If a student opened a 10-minute video and the next video wasn't opened for 45 minutes, we count 10 minutes — the video's own length is the natural cap. If a student opened a 5-minute video but the next video was opened a minute later, we count 1 minute. If a student opened a 20-minute video with only 5 minutes left in the school day, we count 5 minutes.

**A note on unavailable videos:** A portion of videos in any watch history will be unavailable — deleted, made private, or removed by YouTube since they were watched. These videos have no duration data. For these entries, we use the gap to the next video as the elapsed time, capped at 1 minute. This cap is informed by pilot data showing a median video duration of 26 seconds and 83% of videos under 1 minute. In our pilot testing, the vast majority of unavailable video entries had a naturally short gap to the next video and were unaffected by the cap. Only a small fraction of school-hour opens were actually assigned the 1-minute cap, contributing a negligible share of total elapsed time.

**A note on PDF exports:** PDF timestamps are minute-level only, meaning two videos opened in the same minute both show a gap of zero seconds. For this reason, rapid browse counts are reported but flagged as unreliable for PDF data. JSON or HTML exports provide sub-second timestamps and are strongly recommended for accurate rapid browse analysis.

**What this data can and cannot say:**
- ✅ Minimum documented video opens during school hours
- ✅ Evidence of rapid channel-surfing behavior (60-second opens)
- ✅ Conservative floor estimate of time spent on YouTube during class
- ❌ Cannot say exactly how long each video was watched
- ❌ Cannot say whether audio was on or whether the tab was in the background

---

## Data & Privacy

- This tool runs entirely on your own computer. No data is sent anywhere except to the YouTube API to look up video durations (by video ID only — no personal information is sent).
- The watch history file you download contains your child's data. Store it securely on your own device. Only share it if you choose to — no one can access it without your knowledge.
- The `yt_metadata.csv` file contains video titles and channels but no personal information. It can be shared safely.

---

## Citation

If you use this tool in research or advocacy, please cite:

> Bouvier, M. (2026). *Classroom YouTube Tracker* [Software]. NET Lab, Inc. https://github.com/merrybouv/classroom-youtube-tracker

---

## Questions or Issues

Open a GitHub Issue or contact NET Lab at merrybouv@proton.me
