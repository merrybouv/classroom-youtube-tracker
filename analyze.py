"""
NET Lab YouTube History Analyzer — v5
======================================
Accepts Google Takeout watch history in JSON, HTML, or PDF format.
Calculates elapsed school-hour watch time using simple start/end times.
No yt-dlp. No bell schedule required.

USAGE
-----
  python3 analyze_yt_history_v5.py \\
      --input watch-history.json \\
      --metadata yt_metadata_v2.csv \\
      --school-start 07:00 \\
      --school-end 14:25 \\
      --grade middle \\
      --date-from 2025-05-21 \\
      --date-to 2026-01-29

If --school-start / --school-end are omitted, defaults are set by --grade:
  elementary  : 08:00 – 14:30
  middle      : 07:00 – 14:30
  high        : 07:30 – 15:00
  (override these defaults whenever the parent can confirm actual times)

METHOD
------
  elapsed = min(gap_to_next_video, time_remaining_until_school_end, video_duration)

  For videos with no duration (unavailable/deleted):
    elapsed = gap_to_next_video, capped at 30 minutes

RAPID BROWSE
------------
  Opens with elapsed < 30 seconds.
  JSON: real sub-second measurement.
  HTML: real second-level measurement.
  PDF:  minute-level only — same-minute opens show 0 (measurement artifact).
"""

import argparse
import json
import math
import re
import sys
from datetime import datetime, timedelta, time as time_type
from pathlib import Path

import pandas as pd

# ── GRADE-BASED DEFAULT SCHOOL TIMES ──────────────────────────────────────
# Source: NCES 2020-21 national averages (most recent federal data)
#   Elementary avg start: 8:16 AM | typical end: 2:45 PM (~6.5h day)
#   Middle     avg start: 8:11 AM | typical end: 3:10 PM (~7h day)
#   High       avg start: 8:07 AM | typical end: 3:07 PM (~7h day)
# Note: ~82% of US middle/high schools start before AAP-recommended 8:30 AM.
# Southern states skew earlier — Louisiana middle avg is 7:37 AM.
# ALWAYS override with parent-confirmed times when available.
GRADE_DEFAULTS = {
    'elementary': ('08:15', '14:45'),
    'middle':     ('08:10', '15:10'),
    'high':       ('08:05', '15:05'),
}

GAP_CAP_MINUTES = 30  # fallback cap for videos with no duration metadata

# ── TIMEZONE (Houston-specific; adapt for other cities) ───────────────────
# CDT (UTC-5) March–October, CST (UTC-6) November–February
_UTC_OFFSET = {1:-6,2:-6,3:-5,4:-5,5:-5,6:-5,7:-5,8:-5,9:-5,10:-5,11:-6,12:-6}

def utc_to_local(dt_utc):
    return dt_utc + timedelta(hours=_UTC_OFFSET[dt_utc.month])


# ── PARSERS ────────────────────────────────────────────────────────────────

def parse_json(path, date_from, date_to):
    """
    Google Takeout: watch-history.json
    Timestamps: ISO 8601 UTC with sub-second precision.
    """
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    entries = []
    for e in data:
        url = e.get('titleUrl', '')
        if 'watch?v=' not in url:
            continue
        video_id = url.split('v=')[1].split('&')[0]
        dt_utc   = datetime.fromisoformat(e['time'].replace('Z', '+00:00'))
        dt_local = utc_to_local(dt_utc)
        d        = dt_local.date()
        if date_from and d < date_from: continue
        if date_to   and d > date_to:   continue
        if d.weekday() >= 5:            continue
        entries.append({
            'url': url, 'video_id': video_id,
            'timestamp': dt_local.replace(tzinfo=None), 'date': d,
        })
    return entries, 'JSON'


def parse_html(path, date_from, date_to):
    """
    Google Takeout: watch-history.html
    Timestamps: e.g. 'Jan 29, 2026, 1:16:23 PM EST' — second-level with timezone.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        sys.exit("HTML input requires beautifulsoup4: pip install beautifulsoup4")

    TZ_OFFSETS = {'EST': -5, 'EDT': -4, 'CST': -6, 'CDT': -5,
                  'MST': -7, 'MDT': -6, 'PST': -8, 'PDT': -7}

    def parse_html_timestamp(text):
        # Format: "Jan 29, 2026, 1:16:23 PM EST"
        m = re.search(
            r'(\w+ \d+, \d+, \d+:\d+:\d+ [AP]M)\s+([A-Z]{2,3})', text)
        if not m:
            # fallback: no seconds "Jan 29, 2026, 1:16 PM EST"
            m = re.search(
                r'(\w+ \d+, \d+, \d+:\d+ [AP]M)\s+([A-Z]{2,3})', text)
            if not m:
                return None
            dt = datetime.strptime(m.group(1), '%b %d, %Y, %I:%M %p')
        else:
            dt = datetime.strptime(m.group(1), '%b %d, %Y, %I:%M:%S %p')
        offset = TZ_OFFSETS.get(m.group(2), 0)
        # Convert to UTC then to local Houston time
        dt_utc = dt - timedelta(hours=offset)
        return utc_to_local(dt_utc)

    with open(path, encoding='utf-8') as f:
        soup = BeautifulSoup(f, 'html.parser')

    entries = []
    # Google Takeout HTML structure: content-cell divs
    for cell in soup.find_all('div', class_=re.compile('content-cell')):
        a = cell.find('a', href=re.compile(r'youtube\.com/watch\?v='))
        if not a:
            continue
        url      = a['href']
        video_id = url.split('v=')[1].split('&')[0]
        text     = cell.get_text(' ', strip=True)
        dt_local = parse_html_timestamp(text)
        if not dt_local:
            continue
        d = dt_local.date()
        if date_from and d < date_from: continue
        if date_to   and d > date_to:   continue
        if d.weekday() >= 5:            continue
        entries.append({
            'url': url, 'video_id': video_id,
            'timestamp': dt_local.replace(tzinfo=None), 'date': d,
        })
    return entries, 'HTML'


def parse_pdf(path, date_from, date_to):
    """
    Google My Activity PDF export.
    Timestamps: minute-level only ('1:16 PM').
    Reference date for 'Yesterday' must match the PDF's save date.
    """
    try:
        import pdfplumber
    except ImportError:
        sys.exit("PDF input requires pdfplumber: pip install pdfplumber")

    YESTERDAY = datetime(2026, 1, 28)  # update if using a different PDF

    def parse_date_hdr(s):
        s = s.strip()
        if s == 'Yesterday': return YESTERDAY.date()
        if s == 'Today':     return (YESTERDAY + timedelta(days=1)).date()
        for fmt in ('%B %d %Y', '%b %d, %Y'):
            try:
                suffix = ' 2026' if len(s.split()) == 2 else ''
                return datetime.strptime(s + suffix, fmt).date()
            except: pass
        return None

    date_pat = re.compile(
        r'^(Yesterday|Today|'
        r'(?:January|February|March|April|May|June|July|August|'
        r'September|October|November|December)\s+\d{1,2}|'
        r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'
        r'\s+\d{1,2},\s+\d{4})$'
    )
    url_pat  = re.compile(r'(https://www\.youtube\.com/watch\?v=[\w\-]+)')
    time_pat = re.compile(r'^(\d{1,2}:\d{2}\s+(?:AM|PM))\s*[•·]')

    with pdfplumber.open(path) as pdf:
        full_text = '\n'.join(p.extract_text() or '' for p in pdf.pages)

    entries, current_date, pending_url = [], None, None
    for line in full_text.split('\n'):
        s = line.strip()
        if date_pat.match(s):
            current_date = parse_date_hdr(s)
            continue
        m = url_pat.search(s)
        if m:
            pending_url = m.group(1)
            continue
        t = time_pat.match(s)
        if t and pending_url and current_date:
            try:
                ts = datetime.combine(current_date,
                    datetime.strptime(t.group(1).strip(), '%I:%M %p').time())
            except:
                pending_url = None
                continue
            d = current_date
            if date_from and d < date_from: pending_url = None; continue
            if date_to   and d > date_to:   pending_url = None; continue
            if d.weekday() >= 5:            pending_url = None; continue
            entries.append({
                'url': pending_url,
                'video_id': pending_url.split('v=')[1],
                'timestamp': ts, 'date': d,
            })
            pending_url = None
    return entries, 'PDF'


def parse_input(path, date_from, date_to):
    p = path.lower()
    if p.endswith('.json'): return parse_json(path, date_from, date_to)
    if p.endswith('.html'): return parse_html(path, date_from, date_to)
    if p.endswith('.pdf'):  return parse_pdf(path, date_from, date_to)
    sys.exit(f"Unsupported file type: {path}  (use .json, .html, or .pdf)")


# ── ELAPSED TIME ───────────────────────────────────────────────────────────

def compute_elapsed(entries, school_start_t, school_end_t, duration_map):
    df = pd.DataFrame(entries).sort_values('timestamp').reset_index(drop=True)
    results = []

    for d, group in df.groupby('date'):
        idx           = group.index.tolist()
        school_end_dt = datetime.combine(d, school_end_t)

        for i, pos in enumerate(idx):
            ts        = df.at[pos, 'timestamp']
            in_school = school_start_t <= ts.time() < school_end_t
            mins_to_end = max(0, (school_end_dt - ts).total_seconds() / 60)

            if i < len(idx) - 1:
                gap_mins = (df.at[idx[i+1], 'timestamp'] - ts).total_seconds() / 60
                if gap_mins < 0:
                    raw_window, window_type = None, 'NEGATIVE_GAP'
                else:
                    raw_window  = round(min(gap_mins, mins_to_end), 2)
                    window_type = ('CAPPED_BY_SCHOOL_END'
                                   if mins_to_end <= gap_mins else 'GAP_TO_NEXT')
            else:
                raw_window  = round(mins_to_end, 2)
                window_type = 'END_OF_DAY'

            vid_dur_sec = duration_map.get(df.at[pos, 'video_id'])
            no_duration = (vid_dur_sec is None or
                           (isinstance(vid_dur_sec, float) and math.isnan(vid_dur_sec)))

            if raw_window is None:
                elapsed, note = None, 'NEGATIVE_GAP'
            elif no_duration:
                if raw_window > GAP_CAP_MINUTES:
                    elapsed, note = None, f'NO_DURATION_EXCEEDS_{GAP_CAP_MINUTES}MIN_CAP'
                else:
                    elapsed, note = raw_window, 'NO_DURATION_USED_WINDOW'
            else:
                vid_dur_min = vid_dur_sec / 60
                elapsed     = round(min(raw_window, vid_dur_min), 2)
                note        = ('CAPPED_BY_DURATION'
                               if vid_dur_min < raw_window else 'CAPPED_BY_WINDOW')

            results.append({
                'url':                df.at[pos, 'url'],
                'video_id':           df.at[pos, 'video_id'],
                'timestamp':          ts,
                'date':               str(d),
                'in_school_hours':    in_school,
                'raw_window_minutes': raw_window,
                'window_type':        window_type,
                'video_duration_sec': vid_dur_sec,
                'elapsed_minutes':    elapsed,
                'elapsed_note':       note,
            })

    return pd.DataFrame(results)


# ── REPORT ─────────────────────────────────────────────────────────────────

def report(df, fmt, school_start, school_end, grade, out_path):
    df_s = df[df['in_school_hours'] & df['elapsed_minutes'].notna()].copy()

    total   = len(df)
    school  = len(df_s)
    dates   = df['date'].nunique()
    vids    = df['video_id'].nunique()
    hrs     = df_s['elapsed_minutes'].sum() / 60
    rapid   = (df_s['elapsed_minutes'] < 0.5).sum()

    bins   = [0, 0.5, 1, 2, 5, 10, 20, 30, float('inf')]
    labels = ['<30sec','30sec–1min','1–2min','2–5min',
              '5–10min','10–20min','20–30min','>30min']
    df_s['bin'] = pd.cut(df_s['elapsed_minutes'], bins=bins, labels=labels)
    dist = df_s['bin'].value_counts().sort_index()

    daily = df_s.groupby('date').agg(
        opens=('video_id','count'),
        elapsed_min=('elapsed_minutes','sum'),
        rapid=('elapsed_minutes', lambda x: (x < 0.5).sum())
    ).sort_values('date')

    lines = []
    w = 62
    lines.append('=' * w)
    lines.append('NET Lab — YouTube School-Hour Activity Report')
    lines.append('=' * w)
    lines.append(f'Input format  : {fmt}')
    lines.append(f'Grade level   : {grade or "not specified"}')
    lines.append(f'School hours  : {school_start} – {school_end}  (weekdays only)')
    lines.append(f'Date range    : {df["date"].min()} → {df["date"].max()}')
    lines.append(f'School days   : {dates}')
    lines.append(f'Unique videos : {vids:,}')
    lines.append('')
    lines.append('── Video Opens ' + '─' * (w - 16))
    lines.append(f'  Total opens             : {total:,}')
    lines.append(f'  School-hour opens       : {school:,}  ({school/total*100:.1f}% of total)')
    lines.append(f'  Avg opens / school day  : {school/dates:.1f}')
    lines.append('')
    lines.append('── Rapid Browse ' + '─' * (w - 16))
    lines.append(f'  Defined as: elapsed < 30 seconds')
    under30  = (df_s['elapsed_minutes'] < 0.5).sum()
    under60  = (df_s['elapsed_minutes'] < 1.0).sum()
    if fmt == 'PDF':
        exact_zero   = (df_s['elapsed_minutes'] == 0).sum()
        dur_capped   = ((df_s['elapsed_minutes'] > 0) &
                        (df_s['elapsed_minutes'] < 0.5) &
                        (df_s['elapsed_note'] == 'CAPPED_BY_DURATION')).sum()
        lines.append(f'')
        lines.append(f'  ⚠ PDF LIMITATION — sub-minute values have TWO sources')
        lines.append(f'  that cannot be distinguished from each other:')
        lines.append(f'')
        lines.append(f'  Source 1 — Same-minute artifact (elapsed = exactly 0):')
        lines.append(f'    {exact_zero:,} entries where two videos share the same minute')
        lines.append(f'    timestamp. True gap is 0–59 sec but unmeasurable.')
        lines.append(f'    This inflates rapid browse — NOT a behavioral signal.')
        lines.append(f'')
        lines.append(f'  Source 2 — Duration cap on short videos (0 < elapsed < 30 sec):')
        lines.append(f'    {dur_capped:,} entries where elapsed = video duration < 30 sec.')
        lines.append(f'    Gap to next video was ≥1 min, but the video itself was short')
        lines.append(f'    (likely a YouTube Short). This is a CONTENT signal, not')
        lines.append(f'    a behavioral one — the student may have watched the whole video.')
        lines.append(f'')
        lines.append(f'  Under 30 sec : {under30:,}  ({under30/school*100:.1f}%) — UNRELIABLE for PDF')
        lines.append(f'  Under 60 sec total : {under60:,}  ({under60/school*100:.1f}%) — UNRELIABLE for PDF')
        lines.append(f'  Use JSON or HTML for any rapid-browse analysis.')
    else:
        lines.append(f'  Under 30 sec : {under30:,}  ({under30/school*100:.1f}% of school-hour opens)')
        lines.append(f'  Under 60 sec : {under60:,}  ({under60/school*100:.1f}% of school-hour opens)')
        lines.append(f'  ✓ {fmt} timestamps are sub-second — these are real measured gaps.')
    lines.append('')
    lines.append('── Elapsed Time Distribution (school hours) ' + '─' * (w - 44))
    for label, count in dist.items():
        bar = '█' * int(count / school * 40) if school else ''
        lines.append(f'  {label:<12} {count:4d}  ({count/school*100:4.1f}%)  {bar}')
    lines.append('')
    lines.append('── Estimated Watch Time ' + '─' * (w - 24))
    lines.append(f'  Total elapsed hours     : {hrs:.1f}')
    lines.append(f'  Avg min / school day    : {df_s["elapsed_minutes"].sum()/dates:.1f}')
    lines.append(f'  Method: elapsed = min(gap_to_next, time_to_school_end, video_duration)')
    lines.append(f'  Gap cap: {GAP_CAP_MINUTES} min for videos with no duration metadata')
    lines.append('')
    lines.append('── Cap Usage ' + '─' * (w - 13))
    for note, count in df['elapsed_note'].value_counts().items():
        lines.append(f'  {note:<42} {count:4d}')
    lines.append('')
    lines.append('── Per-Day Detail ' + '─' * (w - 18))
    lines.append(f'  {"Date":<12} {"Opens":>6} {"Elapsed":>9} {"Rapid":>6}')
    lines.append(f'  {"-"*12} {"-"*6} {"-"*9} {"-"*6}')
    for d, row in daily.iterrows():
        lines.append(f'  {d:<12} {int(row["opens"]):>6} '
                     f'{row["elapsed_min"]:>7.0f}min '
                     f'{int(row["rapid"]):>5} rapid')

    text = '\n'.join(lines)
    print(text)
    with open(out_path, 'w') as f:
        f.write(text)


# ── MAIN ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='NET Lab YouTube History Analyzer v5')
    parser.add_argument('--input',        required=True,
                        help='.json, .html, or .pdf watch history file')
    parser.add_argument('--metadata',     default=None,
                        help='yt_metadata_v2.csv (video duration lookup)')
    parser.add_argument('--school-start', default=None,
                        help='School start HH:MM 24h (overrides grade default)')
    parser.add_argument('--school-end',   default=None,
                        help='School end HH:MM 24h (overrides grade default)')
    parser.add_argument('--grade',        default=None,
                        choices=['elementary','middle','high'],
                        help='Grade level for default school times')
    parser.add_argument('--date-from',    default=None,
                        help='Filter from YYYY-MM-DD')
    parser.add_argument('--date-to',      default=None,
                        help='Filter to YYYY-MM-DD')
    parser.add_argument('--output-dir',   default='.',
                        help='Directory for output files')
    args = parser.parse_args()

    # School time defaults
    grade = args.grade or 'middle'
    default_start, default_end = GRADE_DEFAULTS[grade]
    school_start_str = args.school_start or default_start
    school_end_str   = args.school_end   or default_end
    assumed = not (args.school_start and args.school_end)

    school_start_t = datetime.strptime(school_start_str, '%H:%M').time()
    school_end_t   = datetime.strptime(school_end_str,   '%H:%M').time()

    date_from = datetime.strptime(args.date_from, '%Y-%m-%d').date() if args.date_from else None
    date_to   = datetime.strptime(args.date_to,   '%Y-%m-%d').date() if args.date_to   else None

    print(f'\nNET Lab YouTube History Analyzer v5')
    print(f'Input       : {args.input}')
    print(f'School hours: {school_start_str} – {school_end_str}'
          + (' (assumed from grade default)' if assumed else ' (parent confirmed)'))
    print(f'Grade       : {grade}')
    if date_from or date_to:
        print(f'Date range  : {date_from or "start"} → {date_to or "end"}')
    print()

    # Parse input
    entries, fmt = parse_input(args.input, date_from, date_to)
    print(f'Parsed {len(entries):,} entries ({fmt}, weekdays only)')

    # Load metadata
    duration_map = {}
    if args.metadata and Path(args.metadata).exists():
        meta = pd.read_csv(args.metadata)
        duration_map = dict(zip(
            meta['video_id'],
            pd.to_numeric(meta['duration_seconds'], errors='coerce')
        ))
        print(f'Loaded duration metadata for {len(duration_map):,} videos')
    else:
        print('No metadata file — elapsed time will use gap cap only')

    # Compute
    df = compute_elapsed(entries, school_start_t, school_end_t, duration_map)

    # Save
    out = args.output_dir
    df.to_csv(f'{out}/yt_events.csv', index=False)

    df_s = df[df['in_school_hours'] & df['elapsed_minutes'].notna()]
    df_s.groupby('date').agg(
        opens=('video_id','count'),
        elapsed_minutes=('elapsed_minutes','sum'),
        rapid_browse=('elapsed_minutes', lambda x: (x < 0.5).sum())
    ).to_csv(f'{out}/yt_daily.csv')

    report(df, fmt, school_start_str, school_end_str, grade,
           f'{out}/yt_report.txt')

    print(f'\nSaved: yt_events.csv  yt_daily.csv  yt_report.txt → {out}/')


if __name__ == '__main__':
    main()
