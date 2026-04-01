"""
Classroom YouTube Tracker — Flask web app
Orchestrates fetch_metadata.py and analyze.py as subprocesses.
"""

import csv
import io
import math
import os
import statistics
import subprocess
import sys
import tempfile
import uuid
from collections import Counter
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from flask import Flask, request, jsonify, Response, abort, render_template_string

load_dotenv()

import logging
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

_api_key_loaded = bool(os.environ.get('YOUTUBE_API_KEY', '').strip())
print(f'API key loaded: {"YES" if _api_key_loaded else "NO — add YOUTUBE_API_KEY to .env"}')

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB

# In-memory store for CSV downloads: uuid → {filename: bytes}
_downloads: dict[str, dict[str, bytes]] = {}

# ── PERSISTENT METADATA CACHE ──────────────────────────────────────────────
CACHE_PATH = Path(__file__).parent / 'yt_metadata_cache.csv'

_CACHE_FIELDS = [
    'video_id', 'title', 'channel', 'category', 'duration_seconds',
    'tags', 'is_kids_content', 'age_limit', 'availability',
]

def _merge_into_cache(temp_csv: str) -> None:
    cache: dict[str, dict] = {}
    if CACHE_PATH.exists():
        with open(CACHE_PATH, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                cache[row['video_id']] = row

    new_entries: dict[str, dict] = {}
    with open(temp_csv, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            new_entries[row['video_id']] = row

    added = [vid for vid in new_entries if vid not in cache]
    if not added:
        logging.info('Cache: no new entries to add (%d already cached)', len(cache))
        return

    for vid in added:
        cache[vid] = new_entries[vid]

    with open(CACHE_PATH, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=_CACHE_FIELDS, extrasaction='ignore')
        writer.writeheader()
        for row in cache.values():
            writer.writerow(row)

    logging.info('Cache: +%d new entries → %d total (%s)', len(added), len(cache), CACHE_PATH)


YT_CATEGORIES = {
    '1':  'Film & Animation',
    '2':  'Autos & Vehicles',
    '10': 'Music',
    '15': 'Pets & Animals',
    '17': 'Sports',
    '18': 'Short Movies',
    '19': 'Travel & Events',
    '20': 'Gaming',
    '21': 'Videoblogging',
    '22': 'People & Blogs',
    '23': 'Comedy',
    '24': 'Entertainment',
    '25': 'News & Politics',
    '26': 'Howto & Style',
    '27': 'Education',
    '28': 'Science & Technology',
    '29': 'Nonprofits & Activism',
    '30': 'Movies',
    '31': 'Anime/Animation',
    '32': 'Action/Adventure',
    '33': 'Classics',
    '34': 'Comedy',
    '35': 'Documentary',
    '36': 'Drama',
    '37': 'Family',
    '38': 'Foreign',
    '39': 'Horror',
    '40': 'Sci-Fi/Fantasy',
    '41': 'Thriller',
    '42': 'Shorts',
    '43': 'Shows',
    '44': 'Trailers',
}

GRADE_DEFAULTS = {
    'elementary': ('08:15', '14:45'),
    'middle':     ('08:10', '15:10'),
    'high':       ('08:05', '15:05'),
}

_ELAPSED_NOTE_LABELS = {
    'NO_DURATION_USED_WINDOW': 'Video length unavailable — gap or cap used',
    'CAPPED_BY_DURATION':      'Capped by video length',
    'CAPPED_BY_WINDOW':        'Capped by school day end',
    'GAP_TO_NEXT':             'Gap to next video',
    'END_OF_DAY':              'Last video of day',
    'NEGATIVE_GAP':            'Timestamp error',
}

# ── TEST FORM ───────────────────────────────────────────────────────────────

_TEST_FORM = """<!doctype html>
<html>
<head><title>Analyze (test)</title></head>
<body>
<h2>Classroom YouTube Tracker — test form</h2>
<form id="f" method="post" action="/analyze" enctype="multipart/form-data">
  <p><label>History file: <input type="file" name="history_file" required></label></p>
  <p><label>Timezone:
    <select name="timezone">
      <option value="eastern">Eastern</option>
      <option value="central">Central</option>
      <option value="mountain">Mountain</option>
      <option value="pacific">Pacific</option>
      <option value="alaska">Alaska</option>
      <option value="hawaii">Hawaii</option>
    </select>
  </label></p>
  <p><label>Grade:
    <select name="grade">
      <option value="elementary">Elementary</option>
      <option value="middle" selected>Middle</option>
      <option value="high">High</option>
    </select>
  </label></p>
  <p><label>School start (HH:MM): <input type="text" name="school_start" placeholder="08:10"></label></p>
  <p><label>School end (HH:MM): <input type="text" name="school_end" placeholder="15:10"></label></p>
  <p><button type="submit">Run Analysis</button></p>
</form>
<pre id="out" style="background:#f4f4f4;padding:12px;white-space:pre-wrap;"></pre>
<script>
document.getElementById('f').addEventListener('submit', async function(e) {
  e.preventDefault();
  document.getElementById('out').textContent = 'Running...';
  const resp = await fetch('/analyze', {method: 'POST', body: new FormData(this)});
  const text = await resp.text();
  try {
    document.getElementById('out').textContent = JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    document.getElementById('out').textContent = text;
  }
});
</script>
</body>
</html>"""


# ── ROUTES ─────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template_string(_TEST_FORM)


@app.route('/analyze', methods=['POST'])
def analyze():
    file = request.files.get('history_file')
    if not file or file.filename == '':
        return jsonify(error='Please select a file.'), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in ('.json', '.html', '.pdf'):
        return jsonify(error='Unsupported file type. Use .json, .html, or .pdf'), 400

    api_key = os.environ.get('YOUTUBE_API_KEY', '').strip()
    if not api_key:
        return jsonify(error='YouTube API key not found. Add YOUTUBE_API_KEY=your_key to .env and restart.'), 500

    timezone     = request.form.get('timezone', 'eastern')
    grade        = request.form.get('grade', 'middle')
    school_start = request.form.get('school_start', '').strip()
    school_end   = request.form.get('school_end', '').strip()

    default_start, default_end = GRADE_DEFAULTS[grade]
    school_start = school_start or default_start
    school_end   = school_end   or default_end

    python     = sys.executable
    script_dir = Path(__file__).parent

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # 1. Save uploaded file
            upload_path = os.path.join(tmpdir, file.filename)
            file.save(upload_path)
            logging.info('Saved upload to %s', upload_path)

            # 2. Run fetch_metadata.py
            metadata_path = os.path.join(tmpdir, 'yt_metadata.csv')
            fetch_cmd = [
                python, str(script_dir / 'fetch_metadata.py'),
                '--input',    upload_path,
                '--existing', str(CACHE_PATH),
                '--output',   metadata_path,
            ]
            logging.info('Running fetch_metadata.py ...')
            fetch_result = subprocess.run(fetch_cmd, capture_output=True, text=True, env={**os.environ})
            logging.info('fetch_metadata stdout:\n%s', fetch_result.stdout)
            if fetch_result.returncode != 0:
                logging.error('fetch_metadata stderr:\n%s', fetch_result.stderr)
                return jsonify(error=f'Metadata fetch failed: {fetch_result.stderr[-500:] or fetch_result.stdout[-500:]}'), 500

            _merge_into_cache(metadata_path)

            # 3. Run analyze.py
            analyze_cmd = [
                python, str(script_dir / 'analyze.py'),
                '--input',        upload_path,
                '--metadata',     metadata_path,
                '--timezone',     timezone,
                '--grade',        grade,
                '--school-start', school_start,
                '--school-end',   school_end,
                '--output-dir',   tmpdir,
            ]
            logging.info('Running analyze.py ...')
            analyze_result = subprocess.run(analyze_cmd, capture_output=True, text=True, env={**os.environ})
            logging.info('analyze stdout:\n%s', analyze_result.stdout)
            if analyze_result.returncode != 0:
                logging.error('analyze stderr:\n%s', analyze_result.stderr)
                return jsonify(error=f'Analysis failed: {analyze_result.stderr[-500:] or analyze_result.stdout[-500:]}'), 500

            # 4. Build metadata lookup
            events_path = os.path.join(tmpdir, 'yt_events.csv')
            daily_path  = os.path.join(tmpdir, 'yt_daily.csv')

            meta_lookup: dict[str, dict] = {}
            if Path(metadata_path).exists():
                with open(metadata_path, newline='', encoding='utf-8') as mf:
                    for mrow in csv.DictReader(mf):
                        meta_lookup[mrow['video_id']] = mrow

            # 5. Compute stats from yt_events.csv
            all_dates:        set[str] = set()
            school_dates:     set[str] = set()
            school_video_ids: set[str] = set()
            elapsed_seconds_list: list[float] = []
            total_opens  = 0
            rapid_count  = 0
            shorts_count = 0
            date_min = date_max = None

            top_video_ctr:    Counter = Counter()
            top_channel_ctr:  Counter = Counter()
            top_category_ctr: Counter = Counter()

            if Path(events_path).exists():
                with open(events_path, newline='', encoding='utf-8') as ef:
                    for erow in csv.DictReader(ef):
                        d         = erow.get('date', '')
                        in_school = erow.get('in_school_hours', '').lower() == 'true'

                        if d:
                            all_dates.add(d)
                            if date_min is None or d < date_min: date_min = d
                            if date_max is None or d > date_max: date_max = d

                        if not in_school:
                            continue

                        school_dates.add(d)
                        total_opens += 1
                        vid     = erow['video_id']
                        school_video_ids.add(vid)
                        m       = meta_lookup.get(vid, {})
                        title   = (m.get('title')   or '').strip() or None
                        channel = (m.get('channel') or '').strip() or None

                        # Top 10 — exclude unavailable
                        if title and channel:
                            top_video_ctr[(vid, title, channel)] += 1
                        if channel:
                            top_channel_ctr[channel] += 1

                        try:
                            elapsed_min = float(erow.get('elapsed_minutes') or 'nan')
                            if not math.isnan(elapsed_min):
                                elapsed_seconds_list.append(elapsed_min * 60)
                                if elapsed_min < 1.0:
                                    rapid_count += 1
                        except (ValueError, TypeError):
                            pass

                        category_raw   = (m.get('category') or '').strip()
                        category_label = YT_CATEGORIES.get(category_raw, 'Unknown')
                        top_category_ctr[category_label] += 1
                        if category_raw == '42':
                            shorts_count += 1

            school_days_count  = len(school_dates)
            total_school_days  = len(all_dates)
            pct_school_days    = school_days_count / total_school_days * 100 if total_school_days else 0.0
            total_elapsed_hrs  = sum(elapsed_seconds_list) / 3600
            mean_elapsed_sec   = statistics.mean(elapsed_seconds_list)   if elapsed_seconds_list else 0.0
            median_elapsed_sec = statistics.median(elapsed_seconds_list) if elapsed_seconds_list else 0.0
            pct_rapid          = rapid_count  / total_opens * 100 if total_opens else 0.0
            pct_shorts         = shorts_count / total_opens * 100 if total_opens else 0.0
            avg_opens_per_day  = total_opens  / school_days_count if school_days_count else 0.0

            top_videos         = [{'title': t, 'channel': c, 'opens': n}
                                  for (_, t, c), n in top_video_ctr.most_common(10)]
            top_channels       = [{'channel': ch, 'opens': n}
                                  for ch, n in top_channel_ctr.most_common(10)]
            category_breakdown = [{'category': cat, 'opens': n}
                                  for cat, n in top_category_ctr.most_common()]

            # 6. Transform events CSV — rename columns, plain English notes, drop internal columns
            df_events = pd.read_csv(events_path)
            df_events['elapsed_note'] = df_events['elapsed_note'].replace(_ELAPSED_NOTE_LABELS)
            df_events = df_events.drop(columns=['in_school_hours', 'raw_window_minutes', 'window_type'],
                                       errors='ignore')
            df_events = df_events.rename(columns={
                'video_duration_sec': 'video_length_seconds',
                'elapsed_minutes':    'estimated_minutes_watched',
                'elapsed_note':       'how_time_was_estimated',
            })
            events_buf = io.StringIO()
            df_events.to_csv(events_buf, index=False)
            events_bytes = events_buf.getvalue().encode('utf-8')

            # 7. Transform daily CSV — rename columns, add pct column
            df_daily = pd.read_csv(daily_path)
            df_daily = df_daily.rename(columns={
                 'opens':           'videos_opened',
                'elapsed_minutes': 'estimated_minutes_watched',
                 'rapid_browse':    'number_of_videos_rapid_browsed',
            })
            df_daily['percent_of_videos_rapid_browsed'] = (
                df_daily['number_of_videos_rapid_browsed'] / df_daily['videos_opened'] * 100
            ).round(1).fillna(0.0)
            daily_buf = io.StringIO()
            df_daily.to_csv(daily_buf, index=False)
            daily_bytes = daily_buf.getvalue().encode('utf-8')

        # Store CSVs in memory for download
        run_id = str(uuid.uuid4())
        _downloads[run_id] = {
            'youtube_detail.csv':        events_bytes,
            'youtube_daily_summary.csv': daily_bytes,
        }

    except Exception as exc:
        logging.exception('Analysis failed')
        return jsonify(error=f'Analysis failed: {exc}'), 500

    return jsonify(
        run_id=run_id,
        total_opens=total_opens,
        school_days_with_activity=school_days_count,
        pct_school_days_with_activity=round(pct_school_days, 1),
        unique_videos=len(school_video_ids),
        date_from=date_min,
        date_to=date_max,
        total_elapsed_hours=round(total_elapsed_hrs, 2),
        mean_elapsed_seconds_per_video=round(mean_elapsed_sec, 1),
        median_elapsed_seconds_per_video=round(median_elapsed_sec, 1),
        pct_rapid_browse=round(pct_rapid, 1),
        pct_shorts=round(pct_shorts, 1),
        avg_opens_per_school_day=round(avg_opens_per_day, 1),
        top_videos=top_videos,
        top_channels=top_channels,
        category_breakdown=category_breakdown,
    )


@app.route('/download/<run_id>/<filename>')
def download(run_id, filename):
    if filename not in ('youtube_detail.csv', 'youtube_daily_summary.csv'):
        abort(404)
    entry = _downloads.get(run_id)
    if not entry or filename not in entry:
        abort(404)
    data = entry[filename]
    entry.pop(filename)
    if not entry:
        _downloads.pop(run_id, None)
    return Response(
        data,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


# ── ENTRY POINT ────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app.run(debug=True)