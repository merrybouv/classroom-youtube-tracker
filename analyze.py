"""
Classroom YouTube Tracker — analyze.py
=======================================
Analyzes a child's Google Takeout YouTube watch history to estimate
time spent on YouTube during school hours.

Accepts: watch-history.json, watch-history.html, or a PDF export.

USAGE
-----
  python analyze.py \\
      --input sample_data/watch-history.json \\
      --metadata yt_metadata.csv \\
      --timezone central \\
      --grade middle \\
      --school-start 07:00 \\
      --school-end 14:25

TIMEZONE OPTIONS
----------------
  eastern   — ET  (NY, FL, GA, PA, OH, NC, VA, and most of the East Coast)
  central   — CT  (TX, IL, TN, AL, MS, MO, MN, WI, and most of the Midwest/South)
  mountain  — MT  (CO, AZ, NM, UT, ID, MT, WY)
  pacific   — PT  (CA, WA, OR, NV)
  alaska    — AKT (AK)
  hawaii    — HT  (HI)

  Not sure? Your phone's clock app shows your timezone.
  If your state splits zones (e.g. parts of Indiana or Kansas), pick the
  zone your school is in.

SCHOOL TIME DEFAULTS (NCES 2020-21 national averages)
------------------------------------------------------
  elementary : 08:15 - 14:45
  middle     : 08:10 - 15:10
  high       : 08:05 - 15:05
  Override with --school-start and --school-end whenever possible.

METHOD
------
  elapsed = min(gap_to_next_video, time_remaining_until_school_end, video_duration)

  For unavailable/deleted videos (no duration data):
    elapsed = gap_to_next_video, capped at 1 minute

RAPID BROWSE
------------
  Defined as elapsed < 30 seconds.
  JSON/HTML: real measured gaps — reliable.
  PDF: minute-level timestamps only — rapid browse counts NOT reliable for PDF.

OUTPUTS
-------
  yt_report.txt   — summary report, start here
  yt_events.csv   — one row per video open with elapsed time
  yt_daily.csv    — per-day totals (opens, elapsed minutes, rapid browse)
"""

import argparse
import json
import math
import re
import sys
from datetime import datetime, timedelta, time as time_type, timezone as dt_timezone
from pathlib import Path

import pandas as pd

# ── GRADE-BASED DEFAULT SCHOOL TIMES ──────────────────────────────────────
# Source: NCES 2020-21 national averages.
# ~82% of US middle/high schools start before the AAP-recommended 8:30 AM.
# Always override with parent-confirmed times when available.
GRADE_DEFAULTS = {
    'elementary': ('08:15', '14:45'),
    'middle':     ('08:10', '15:10'),
    'high':       ('08:05', '15:05'),
}


# ── TIMEZONE ───────────────────────────────────────────────────────────────
# Plain English options → IANA timezone names
# JSON exports store timestamps in UTC and must be converted to local time.
# PDF exports are already in local time (printed from the browser) — no conversion needed.
TIMEZONE_OPTIONS = {
    'eastern':  'America/New_York',
    'central':  'America/Chicago',
    'mountain': 'America/Denver',
    'pacific':  'America/Los_Angeles',
    'alaska':   'America/Anchorage',
    'hawaii':   'Pacific/Honolulu',
}

def get_tz_name(tz_input):
    """Convert plain English timezone to IANA name."""
    if not tz_input:
        print("Note: --timezone not provided. Defaulting to Eastern Time.")
        print("      Options: eastern, central, mountain, pacific, alaska, hawaii\n")
        return 'America/New_York'
    key = tz_input.strip().lower()
    if key in TIMEZONE_OPTIONS:
        return TIMEZONE_OPTIONS[key]
    # Be helpful if they pass something unexpected
    print(f"Warning: unrecognised timezone '{tz_input}'.")
    print(f"Valid options: {', '.join(TIMEZONE_OPTIONS.keys())}")
    print("Defaulting to Eastern Time.\n")
    return 'America/New_York'

def utc_to_local(dt_utc, tz_name):
    """Convert a UTC datetime to local time using the given IANA timezone name."""
    try:
        from zoneinfo import ZoneInfo
        return dt_utc.astimezone(ZoneInfo(tz_name)).replace(tzinfo=None)
    except ImportError:
        pass
    try:
        import pytz
        return dt_utc.astimezone(pytz.timezone(tz_name)).replace(tzinfo=None)
    except ImportError:
        import warnings
        warnings.warn(
            "Could not load timezone library — timestamps left in UTC.\n"
            "Fix: pip install pytz  (or upgrade to Python 3.9+)"
        )
        return dt_utc.replace(tzinfo=None)


# ── PARSERS ────────────────────────────────────────────────────────────────

def parse_json(path, date_from, date_to, tz_name):
    """
    Google Takeout: watch-history.json
    Timestamps are UTC with sub-second precision — converted to local time.
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
        dt_local = utc_to_local(dt_utc, tz_name)
        d        = dt_local.date()
        if date_from and d < date_from: continue
        if date_to   and d > date_to:   continue
        if d.weekday() >= 5:            continue  # skip weekends
        entries.append({'url': url, 'video_id': video_id,
                        'timestamp': dt_local, 'date': d})
    return entries, 'JSON'


def parse_html(path, date_from, date_to, tz_name):
    """
    Google Takeout: watch-history.html
    Timestamps include timezone abbreviation (EST, CDT etc.) — converted to local time.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        sys.exit("HTML input requires beautifulsoup4: pip install beautifulsoup4")

    TZ_OFFSETS = {
        'EST': -5, 'EDT': -4, 'CST': -6, 'CDT': -5,
        'MST': -7, 'MDT': -6, 'PST': -8, 'PDT': -7,
        'AKST': -9, 'AKDT': -8, 'HST': -10,
    }

    def parse_ts(text):
        m = re.search(r'(\w+ \d+, \d+, \d+:\d+:\d+ [AP]M)\s+([A-Z]{2,4})', text)
        if not m:
            m = re.search(r'(\w+ \d+, \d+, \d+:\d+ [AP]M)\s+([A-Z]{2,4})', text)
            if not m: return None
            dt = datetime.strptime(m.group(1), '%b %d, %Y, %I:%M %p')
        else:
            dt = datetime.strptime(m.group(1), '%b %d, %Y, %I:%M:%S %p')
        offset = TZ_OFFSETS.get(m.group(2), 0)
        dt_utc = dt.replace(tzinfo=dt_timezone.utc) - timedelta(hours=offset)
        return utc_to_local(dt_utc, tz_name)

    with open(path, encoding='utf-8') as f:
        soup = BeautifulSoup(f, 'html.parser')

    entries = []
    for cell in soup.find_all('div', class_=re.compile('content-cell')):
        a = cell.find('a', href=re.compile(r'youtube\.com/watch\?v='))
        if not a: continue
        url      = a['href']
        video_id = url.split('v=')[1].split('&')[0]
        dt_local = parse_ts(cell.get_text(' ', strip=True))
        if not dt_local: continue
        d = dt_local.date()
        if date_from and d < date_from: continue
        if date_to   and d > date_to:   continue
        if d.weekday() >= 5:            continue
        entries.append({'url': url, 'video_id': video_id,
                        'timestamp': dt_local, 'date': d})
    return entries, 'HTML'


def parse_pdf(path, date_from, date_to):
    """
    Google My Activity PDF export.
    Timestamps are already in local time (printed from the browser) — no conversion needed.
    Precision: minute-level only.
    """
    try:
        import pdfplumber
    except ImportError:
        sys.exit("PDF input requires pdfplumber: pip install pdfplumber")

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

    # Infer the reference date for "Yesterday" from the most recent dated entry
    explicit_dates = []
    for line in full_text.split('\n'):
        s = line.strip()
        for fmt in ('%B %d %Y', '%b %d, %Y'):
            try:
                explicit_dates.append(
                    datetime.strptime(
                        s + (' 2026' if len(s.split()) == 2 else ''), fmt
                    ).date()
                )
            except: pass
    yesterday = (max(explicit_dates) + timedelta(days=1)
                 if explicit_dates else datetime.today().date() - timedelta(days=1))

    def parse_date_hdr(s):
        s = s.strip()
        if s == 'Yesterday': return yesterday
        if s == 'Today':     return yesterday + timedelta(days=1)
        for fmt in ('%B %d %Y', '%b %d, %Y'):
            try:
                return datetime.strptime(
                    s + (' 2026' if len(s.split()) == 2 else ''), fmt
                ).date()
            except: pass
        return None

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


def parse_input(path, date_from, date_to, tz_name):
    p = path.lower()
    if p.endswith('.json'): return parse_json(path, date_from, date_to, tz_name)
    if p.endswith('.html'): return parse_html(path, date_from, date_to, tz_name)
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
            ts = df.at[pos, 'timestamp']
            if hasattr(ts, 'tzinfo') and ts.tzinfo is not None:
                ts = ts.replace(tzinfo=None)

            in_school   = school_start_t <= ts.time() < school_end_t
            mins_to_end = max(0, (school_end_dt - ts).total_seconds() / 60)

            if i < len(idx) - 1:
                next_ts = df.at[idx[i + 1], 'timestamp']
                if hasattr(next_ts, 'tzinfo') and next_ts.tzinfo is not None:
                    next_ts = next_ts.replace(tzinfo=None)
                gap_mins = (next_ts - ts).total_seconds() / 60
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
                # No duration data (video unavailable/deleted).
                # Use the gap directly, capped at 1 minute.
                # Cap is based on case study data showing 95% of videos under 5 min
                # and median elapsed time of ~17 seconds — the cap may slightly
                # overestimate these entries but affects a small fraction of total time.
                elapsed = round(min(raw_window, 1.0), 2)
                note    = 'NO_DURATION_USED_WINDOW'
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

def report(df, fmt, school_start, school_end, grade, timezone, out_path):
    df_s = df[df['in_school_hours'] & df['elapsed_minutes'].notna()].copy()

    total  = len(df)
    school = len(df_s)
    dates  = df['date'].nunique()
    vids   = df['video_id'].nunique()
    hrs    = df_s['elapsed_minutes'].sum() / 60

    bins   = [0, 0.5, 1, 2, 5, 10, 20, 30, float('inf')]
    labels = ['<30sec', '30sec-1min', '1-2min', '2-5min',
              '5-10min', '10-20min', '20-30min', '>30min']
    df_s['bin'] = pd.cut(df_s['elapsed_minutes'], bins=bins, labels=labels)
    dist = df_s['bin'].value_counts().sort_index()

    daily = df_s.groupby('date').agg(
        opens=('video_id', 'count'),
        elapsed_min=('elapsed_minutes', 'sum'),
        rapid=('elapsed_minutes', lambda x: (x < 0.5).sum())
    ).sort_values('date')

    under30 = (df_s['elapsed_minutes'] < 0.5).sum()
    under60 = (df_s['elapsed_minutes'] < 1.0).sum()
    w = 62

    lines = []
    lines.append('=' * w)
    lines.append('Classroom YouTube Tracker -- School-Hour Activity Report')
    lines.append('=' * w)
    lines.append(f'Input format  : {fmt}')
    lines.append(f'Timezone      : {timezone or "not specified (defaulted to Eastern)"}')
    lines.append(f'Grade level   : {grade or "not specified"}')
    lines.append(f'School hours  : {school_start} - {school_end}  (weekdays only)')
    lines.append(f'Date range    : {df["date"].min()} to {df["date"].max()}')
    lines.append(f'School days   : {dates}')
    lines.append(f'Unique videos : {vids:,}')
    lines.append('')
    lines.append('-- Video Opens ' + '-' * (w - 15))
    lines.append(f'  Total opens             : {total:,}')
    lines.append(f'  School-hour opens       : {school:,}  ({school/total*100:.1f}% of total)')
    lines.append(f'  Avg opens / school day  : {school/dates:.1f}')
    lines.append('')
    lines.append('-- Rapid Browse (elapsed < 30 seconds) ' + '-' * (w - 39))
    if fmt == 'PDF':
        exact_zero = (df_s['elapsed_minutes'] == 0).sum()
        dur_capped = (
            (df_s['elapsed_minutes'] > 0) &
            (df_s['elapsed_minutes'] < 0.5) &
            (df_s['elapsed_note'] == 'CAPPED_BY_DURATION')
        ).sum()
        lines.append('  WARNING: PDF rapid browse counts are not reliable.')
        lines.append(f'    {exact_zero:,} same-minute entries -- true gap is 0-59 sec, unmeasurable.')
        lines.append(f'    {dur_capped:,} short video entries -- video itself was under 30 sec.')
        lines.append('    Use JSON or HTML export for reliable rapid-browse analysis.')
        lines.append(f'  Under 30 sec : {under30:,}  ({under30/school*100:.1f}%) -- unreliable for PDF')
    else:
        lines.append(f'  Under 30 sec : {under30:,}  ({under30/school*100:.1f}% of school-hour opens)')
        lines.append(f'  Under 60 sec : {under60:,}  ({under60/school*100:.1f}% of school-hour opens)')
        lines.append(f'  {fmt} timestamps are precise -- these are real measured gaps.')
    lines.append('')
    lines.append('-- Elapsed Time Distribution (school hours) ' + '-' * (w - 44))
    for label, count in dist.items():
        bar = '|' * int(count / school * 40) if school else ''
        lines.append(f'  {label:<12} {count:4d}  ({count/school*100:4.1f}%)  {bar}')
    lines.append('')
    lines.append('-- Estimated Watch Time ' + '-' * (w - 24))
    lines.append(f'  Total elapsed hours      : {hrs:.1f}')
    lines.append(f'  Avg minutes / school day : {df_s["elapsed_minutes"].sum()/dates:.1f}')
    lines.append(f'  Method: elapsed = min(gap_to_next, time_to_school_end, video_duration)')
    lines.append("  Unavailable videos: gap-based, capped at 1 min (see methodology notes)")
    lines.append('')
    lines.append('-- Cap Usage ' + '-' * (w - 13))
    for note, count in df['elapsed_note'].value_counts().items():
        if pd.notna(note):
            lines.append(f'  {str(note):<42} {count:4d}')
    lines.append('')
    lines.append('-- Per-Day Detail ' + '-' * (w - 18))
    lines.append(f'  {"Date":<12} {"Opens":>6} {"Elapsed":>9} {"Rapid<30s":>9}')
    lines.append(f'  {"-"*12} {"-"*6} {"-"*9} {"-"*9}')
    for d, row in daily.iterrows():
        lines.append(f'  {d:<12} {int(row["opens"]):>6} '
                     f'{row["elapsed_min"]:>7.0f}min '
                     f'{int(row["rapid"]):>8}')

    text = '\n'.join(lines)
    print(text)
    with open(out_path, 'w') as f:
        f.write(text)


# ── MAIN ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Classroom YouTube Tracker -- analyze school-hour YouTube activity')
    parser.add_argument('--input',        required=True,
                        help='Path to watch-history.json, .html, or .pdf')
    parser.add_argument('--metadata',     default=None,
                        help='Path to yt_metadata.csv (from fetch_metadata.py)')
    parser.add_argument('--timezone',     default=None,
                        choices=['eastern', 'central', 'mountain',
                                 'pacific', 'alaska', 'hawaii'],
                        help='Your school\'s timezone. '
                             'Options: eastern, central, mountain, pacific, alaska, hawaii')
    parser.add_argument('--grade',        default=None,
                        choices=['elementary', 'middle', 'high'],
                        help='Sets default school times if --school-start/end not provided')
    parser.add_argument('--school-start', default=None,
                        help='School start time HH:MM 24h e.g. 07:30')
    parser.add_argument('--school-end',   default=None,
                        help='School end time HH:MM 24h e.g. 14:30')
    parser.add_argument('--date-from',    default=None,
                        help='Only include dates from YYYY-MM-DD onward')
    parser.add_argument('--date-to',      default=None,
                        help='Only include dates up to YYYY-MM-DD')
    parser.add_argument('--output-dir',   default='.',
                        help='Where to save output files (default: current folder)')
    args = parser.parse_args()

    tz_name = get_tz_name(args.timezone)

    grade            = args.grade or 'middle'
    default_start, default_end = GRADE_DEFAULTS[grade]
    school_start_str = args.school_start or default_start
    school_end_str   = args.school_end   or default_end
    assumed          = not (args.school_start and args.school_end)

    school_start_t = datetime.strptime(school_start_str, '%H:%M').time()
    school_end_t   = datetime.strptime(school_end_str,   '%H:%M').time()

    date_from = datetime.strptime(args.date_from, '%Y-%m-%d').date() if args.date_from else None
    date_to   = datetime.strptime(args.date_to,   '%Y-%m-%d').date() if args.date_to   else None

    print(f'\nClassroom YouTube Tracker')
    print(f'Input       : {args.input}')
    print(f'Timezone    : {args.timezone or "not specified"} -> {tz_name}')
    print(f'School hours: {school_start_str} - {school_end_str}'
          + (' (grade default -- override with --school-start and --school-end)' if assumed else ''))
    if date_from or date_to:
        print(f'Date filter : {date_from or "start"} to {date_to or "end"}')
    print()

    entries, fmt = parse_input(args.input, date_from, date_to, tz_name)
    print(f'Parsed {len(entries):,} entries ({fmt}, weekdays only)')

    duration_map = {}
    if args.metadata and Path(args.metadata).exists():
        meta = pd.read_csv(args.metadata)
        duration_map = dict(zip(
            meta['video_id'],
            pd.to_numeric(meta['duration_seconds'], errors='coerce')
        ))
        print(f'Loaded duration data for {len(duration_map):,} videos')
    else:
        print('No metadata file -- elapsed time gap-based, capped at 1 min')

    df  = compute_elapsed(entries, school_start_t, school_end_t, duration_map)
    out = args.output_dir
    df.to_csv(f'{out}/yt_events.csv', index=False)

    df[df['in_school_hours'] & df['elapsed_minutes'].notna()].groupby('date').agg(
        opens=('video_id', 'count'),
        elapsed_minutes=('elapsed_minutes', 'sum'),
        rapid_browse=('elapsed_minutes', lambda x: (x < 0.5).sum())
    ).to_csv(f'{out}/yt_daily.csv')

    report(df, fmt, school_start_str, school_end_str, grade, args.timezone,
           f'{out}/yt_report.txt')

    print(f'\nSaved: yt_report.txt  yt_events.csv  yt_daily.csv -> {out}/')


if __name__ == '__main__':
    main()
