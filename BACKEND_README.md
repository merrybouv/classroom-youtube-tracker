# Classroom YouTube Tracker — API Backend

Private Flask API backend for the Classroom YouTube Tracker.

## What it does

Accepts a watch history file upload, fetches YouTube video metadata, runs the analysis, and returns school-hour usage statistics as JSON. Two CSV files are available for download after each analysis run.

## Endpoints

**POST /analyze** — accepts multipart form upload with fields:
- `history_file` — watch-history.json, .html, or .pdf
- `timezone` — eastern, central, mountain, pacific, alaska, hawaii
- `grade` — elementary, middle, or high
- `school_start` — HH:MM (24hr)
- `school_end` — HH:MM (24hr)

Returns JSON with stats and a `run_id` for CSV downloads.

**GET /download/<run_id>/youtube_detail.csv**
**GET /download/<run_id>/youtube_daily_summary.csv**

## Setup

1. Clone this repo
2. Install dependencies: `pip install -r requirements.txt`
3. Add your YouTube API key to `.env` (see `.env.example`)
4. Run: `python3 app.py`

## Methodology

See the public repo for full methodology documentation:
https://github.com/merrybouv/classroom-youtube-tracker