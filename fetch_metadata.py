"""
NET Lab — YouTube Metadata Fetcher (YouTube Data API v3)
=========================================================
Fetches video duration, title, channel, and category for a list of
video IDs using the official YouTube Data API v3.

No yt-dlp required. Safe to run on a hosted server.

SETUP
-----
1. Go to https://console.cloud.google.com
2. Create a project → Enable "YouTube Data API v3"
3. Create an API key (Credentials → Create Credentials → API Key)
4. Set environment variable:
     export YOUTUBE_API_KEY=your_key_here
   Or pass via --api-key argument.

QUOTA
-----
YouTube Data API v3 gives 10,000 units/day free.
This script uses the videos.list endpoint: 1 unit per call of up to 50 IDs.
1,862 unique videos = 38 API calls = 38 units. Well within free tier.

USAGE
-----
  python3 fetch_metadata_api.py \\
      --input watch-history.json \\
      --existing yt_metadata.csv \\
      --output yt_metadata_v2.csv

  # Also accepts PDF or HTML:
  python3 fetch_metadata_api.py --input watch-history.html

  # With explicit API key:
  python3 fetch_metadata_api.py --input watch-history.json --api-key AIza...

OUTPUT FIELDS
-------------
  video_id, title, channel, category, duration_seconds,
  tags, is_kids_content, age_limit, availability
"""

import argparse
import csv
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta

try:
    import requests
except ImportError:
    sys.exit("requests required: pip install requests")

# ── PARSE INPUT FILE (extract unique video IDs) ────────────────────────────

def extract_ids_from_json(path):
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    ids = []
    seen = set()
    for e in data:
        url = e.get('titleUrl', '')
        if 'watch?v=' not in url:
            continue
        vid = url.split('v=')[1].split('&')[0]
        if vid not in seen:
            ids.append(vid)
            seen.add(vid)
    return ids

def extract_ids_from_html(path):
    """
    Parse Google Takeout watch-history.html
    Format: <a href="https://www.youtube.com/watch?v=ID">Title</a>
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        sys.exit("HTML input requires beautifulsoup4: pip install beautifulsoup4")
    with open(path, encoding='utf-8') as f:
        soup = BeautifulSoup(f, 'html.parser')
    ids = []
    seen = set()
    for a in soup.find_all('a', href=re.compile(r'youtube\.com/watch\?v=')):
        vid = a['href'].split('v=')[1].split('&')[0]
        if vid not in seen:
            ids.append(vid)
            seen.add(vid)
    return ids

def extract_ids_from_pdf(path):
    try:
        import pdfplumber
    except ImportError:
        sys.exit("PDF input requires pdfplumber: pip install pdfplumber")
    url_pat = re.compile(r'watch\?v=([\w\-]+)')
    with pdfplumber.open(path) as pdf:
        text = '\n'.join(p.extract_text() or '' for p in pdf.pages)
    seen = set()
    ids  = []
    for vid in url_pat.findall(text):
        if vid not in seen:
            ids.append(vid)
            seen.add(vid)
    return ids

def extract_video_ids(path):
    p = path.lower()
    if p.endswith('.json'):   return extract_ids_from_json(path)
    if p.endswith('.html'):   return extract_ids_from_html(path)
    if p.endswith('.pdf'):    return extract_ids_from_pdf(path)
    sys.exit(f"Unsupported file type: {path}. Use .json, .html, or .pdf")


# ── LOAD EXISTING METADATA ─────────────────────────────────────────────────

def load_existing(path):
    """Load already-fetched metadata to avoid repeat API calls."""
    if not path or not Path(path).exists():
        return {}
    existing = {}
    with open(path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            existing[row['video_id']] = row
    print(f"Loaded {len(existing)} existing metadata entries from {path}")
    return existing


# ── ISO 8601 DURATION → SECONDS ───────────────────────────────────────────

def iso_duration_to_seconds(iso):
    """Convert PT4M13S → 253 seconds"""
    if not iso:
        return None
    m = re.match(
        r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', iso
    )
    if not m:
        return None
    h = int(m.group(1) or 0)
    mn = int(m.group(2) or 0)
    s = int(m.group(3) or 0)
    return h * 3600 + mn * 60 + s


# ── YOUTUBE DATA API v3 ────────────────────────────────────────────────────

API_URL = 'https://www.googleapis.com/youtube/v3/videos'
BATCH_SIZE = 50  # max IDs per API call

def fetch_batch(video_ids, api_key):
    """Fetch metadata for up to 50 video IDs in one API call."""
    params = {
        'part':  'snippet,contentDetails,status',
        'id':    ','.join(video_ids),
        'key':   api_key,
    }
    resp = requests.get(API_URL, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()

def parse_api_response(data, requested_ids):
    """
    Parse API response into a dict of video_id → metadata row.
    Videos not in the response are unavailable/deleted/private.
    """
    found = {}
    for item in data.get('items', []):
        vid     = item['id']
        snippet = item.get('snippet', {})
        details = item.get('contentDetails', {})
        status  = item.get('status', {})

        tags = snippet.get('tags', []) or []
        cats = snippet.get('categoryId')  # numeric ID — map below if needed

        found[vid] = {
            'video_id':         vid,
            'title':            snippet.get('title', ''),
            'channel':          snippet.get('channelTitle', ''),
            'category':         snippet.get('categoryId', ''),
            'duration_seconds': iso_duration_to_seconds(details.get('duration')),
            'tags':             '|'.join(tags[:50]),  # cap tag list
            'is_kids_content':  str(snippet.get('madeForKids', False)),
            'age_limit':        '18' if status.get('contentRating') else '0',
            'availability':     'public',
        }

    # IDs not returned = unavailable
    for vid in requested_ids:
        if vid not in found:
            found[vid] = {
                'video_id':         vid,
                'title':            None,
                'channel':          None,
                'category':         None,
                'duration_seconds': None,
                'tags':             None,
                'is_kids_content':  None,
                'age_limit':        None,
                'availability':     'unavailable',
            }

    return found

OUTPUT_FIELDS = [
    'video_id', 'title', 'channel', 'category', 'duration_seconds',
    'tags', 'is_kids_content', 'age_limit', 'availability'
]

def fetch_all_metadata(video_ids, api_key, existing):
    """
    Fetch metadata for all video IDs, skipping those already in existing.
    Returns combined dict of all metadata.
    """
    to_fetch = [v for v in video_ids if v not in existing]
    print(f"Total unique IDs : {len(video_ids)}")
    print(f"Already have     : {len(existing)}")
    print(f"Need API calls   : {len(to_fetch)}")
    print(f"API calls needed : {math.ceil(len(to_fetch) / BATCH_SIZE)} "
          f"(batches of {BATCH_SIZE})")

    results = dict(existing)  # start with what we have

    batches = [to_fetch[i:i+BATCH_SIZE] for i in range(0, len(to_fetch), BATCH_SIZE)]
    for i, batch in enumerate(batches):
        print(f"  Batch {i+1}/{len(batches)} ({len(batch)} IDs)...", end=' ', flush=True)
        try:
            data   = fetch_batch(batch, api_key)
            parsed = parse_api_response(data, batch)
            results.update(parsed)
            public = sum(1 for v in parsed.values() if v['availability'] == 'public')
            print(f"{public}/{len(batch)} public")
        except requests.HTTPError as e:
            print(f"HTTP error: {e}")
        except Exception as e:
            print(f"Error: {e}")
        if i < len(batches) - 1:
            time.sleep(0.5)  # gentle rate limiting

    return results


# ── MAIN ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Fetch YouTube metadata using Data API v3 (no yt-dlp)')
    parser.add_argument('--input',    required=True,
                        help='watch-history.json, watch-history.html, or PDF')
    parser.add_argument('--existing', default=None,
                        help='Existing yt_metadata.csv to reuse (avoids repeat calls)')
    parser.add_argument('--output',   default='yt_metadata_v2.csv',
                        help='Output CSV path (default: yt_metadata_v2.csv)')
    parser.add_argument('--api-key',  default=None,
                        help='YouTube Data API v3 key (or set YOUTUBE_API_KEY env var)')
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get('YOUTUBE_API_KEY')
    if not api_key:
        sys.exit(
            "YouTube API key required.\n"
            "Set YOUTUBE_API_KEY environment variable or use --api-key.\n"
            "Get a free key at: https://console.cloud.google.com"
        )

    print(f"\nNET Lab Metadata Fetcher — YouTube Data API v3")
    print(f"Input  : {args.input}")
    print(f"Output : {args.output}\n")

    video_ids = extract_video_ids(args.input)
    print(f"Extracted {len(video_ids)} unique video IDs from input file")

    existing = load_existing(args.existing)
    results  = fetch_all_metadata(video_ids, api_key, existing)

    # Write output
    with open(args.output, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction='ignore')
        writer.writeheader()
        for vid in video_ids:
            if vid in results:
                writer.writerow(results[vid])

    # Summary
    all_rows   = list(results.values())
    public     = sum(1 for r in all_rows if r.get('availability') == 'public')
    unavail    = sum(1 for r in all_rows if r.get('availability') == 'unavailable')
    print(f"\nDone.")
    print(f"  Total written  : {len(video_ids)}")
    print(f"  Public         : {public}")
    print(f"  Unavailable    : {unavail}")
    print(f"  Saved to       : {args.output}")


if __name__ == '__main__':
    main()
