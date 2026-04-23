# Classroom YouTube Tracker

A Python tool for analyzing YouTube watch history from school Google accounts, 
estimating time spent on YouTube during school hours and characterizing browsing 
behavior. This repository contains the documented methodology and reference code for 
the Classroom YouTube Tracker, developed as part of ongoing research on 
EdTech platform accountability in K–12 education.

**For parents:** A browser-based application built on this methodology will be
available soon at [link]. No Python or technical setup required.

---

## Overview

Google Takeout records when a YouTube video was opened but not how long it was 
watched. This tool estimates watch time using the formula:
```
elapsed = min(gap_to_next_video, time_remaining_until_school_end, video_duration)
```

For unavailable or deleted videos (no duration data), elapsed time is set to the 
gap to the next video, capped at 1 minute.

Full methodology decisions, rationale, and limitations are documented in 
[ASSUMPTIONS.md](ASSUMPTIONS.md).

---

## Requirements

- Python 3.10+
- YouTube Data API v3 key (free, ~10 minutes to set up)
- Dependencies: `pip install -r requirements.txt`

---

## Usage

**Step 1 — Fetch video metadata:**
```bash
export YOUTUBE_API_KEY=your_key_here
python fetch_metadata.py --input watch-history.json
```

**Step 2 — Run the analysis:**
```bash
python analyze.py \
  --input watch-history.json \
  --metadata yt_metadata.csv \
  --school-start 07:00 \
  --school-end 15:00 \
  --grade middle \
  --timezone eastern
```

**Options:**

| Flag | Description | Default |
|------|-------------|---------|
| `--input` | watch-history.json, .html, or .pdf | required |
| `--metadata` | Output from fetch_metadata.py | optional |
| `--school-start` | School start time HH:MM | Grade-level NCES average |
| `--school-end` | School end time HH:MM | Grade-level NCES average |
| `--grade` | elementary, middle, or high | middle |
| `--timezone` | eastern, central, mountain, pacific, alaska, hawaii | eastern |
| `--date-from` | Filter start date YYYY-MM-DD | none |
| `--date-to` | Filter end date YYYY-MM-DD | none |
| `--output-dir` | Output directory | current folder |

**Outputs:**

| File | Contents |
|------|----------|
| `yt_report.txt` | Summary report |
| `yt_events.csv` | One row per video open with elapsed time |
| `yt_daily.csv` | Per-day totals |

---

## Input Formats

| Format | Timestamp precision | Rapid browse measurement |
|--------|-------------------|------------------------|
| JSON | Sub-second (UTC) | ✅ Precise |
| HTML | Second-level (local time) | ✅ Precise |
| PDF | Minute-level (local time) | ⚠️ Imprecise — two videos opened in the same minute both show a gap of zero |

---

## What This Data Can and Cannot Say

- ✅ Minimum documented video opens during school hours on weekdays
- ✅ Evidence of rapid channel-surfing behavior (60-second threshold)
- ✅ Bounded estimate of time spent on YouTube during the school day
- ❌ Cannot distinguish between a school-issued device and a personal 
  device logged into the same school Google account
- ❌ Cannot account for school closures, holidays, or absences
- ❌ Cannot say exactly how long each video was watched
- ❌ Cannot say whether audio was on or the tab was in the background

---

## Parent-Facing Web Application

A browser-based version of this tool will be available soon for non-technical users. 
Built by parent advocate [insert first, last name] in collaboration with this research, 
the application applies the same methodology and produces a visual report with 
charts, category breakdowns, and downloadable data. This repository serves as 
the citable reference implementation validated against the web application outputs.

[Link — coming soon]

---

## Data & Privacy

This tool runs entirely on your own computer. The only external call is to the 
YouTube Data API v3 to retrieve video metadata (by video ID only — no personal 
information is transmitted). Watch history files are never uploaded anywhere.

---

## Citation

Bouvier, M. (2026). *Classroom YouTube Tracker* [Software]. NET Lab, Inc.
https://github.com/merrybouv/classroom-youtube-tracker

Please do not modify core measurement assumptions without documenting the changes. 
Commercial use of this tool or methodology requires a separate license. Contact classroomtechtransparency@gmail.com to inquire.

---

## Questions or Issues

Open a GitHub Issue or contact Meredith Bouvier at merrybouv@proton.me
