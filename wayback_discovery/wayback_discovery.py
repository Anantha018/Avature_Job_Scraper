"""
wayback_discovery.py
======================
Fetches all JobDetail URLs from Wayback Machine CDX API.
Compares against existing URLs and saves only new ones.

Usage:
    python wayback_discovery.py --urls Urls.txt --output wayback_new_jobs.txt
    python wayback_discovery.py --urls Urls.txt --output wayback_new_jobs.txt --from 20190101 --to 20231231
"""

import argparse
import re
import requests

HEADERS = {'User-Agent': 'Mozilla/5.0'}


def fetch_wayback(date_from=None, date_to=None, limit=500000):
    url = (
        'http://web.archive.org/cdx/search/cdx'
        '?url=*.avature.net/*/JobDetail/*'
        '&output=text'
        '&fl=original'
        '&collapse=urlkey'
        f'&limit={limit}'
    )
    if date_from:
        url += f'&from={date_from}'
    if date_to:
        url += f'&to={date_to}'

    print(f'[Wayback] Fetching (limit={limit})...')
    job_urls = set()
    lines_read = 0

    try:
        with requests.get(url, headers=HEADERS, timeout=300, stream=True) as r:
            print(f'[Wayback] Status: {r.status_code}')
            if r.status_code != 200:
                print(f'[Wayback] Failed — try again in a few minutes')
                return set()
            for line in r.iter_lines():
                if not line:
                    continue
                line = line.decode('utf-8', errors='replace').strip()
                lines_read += 1
                if 'JobDetail' in line and re.search(r'/JobDetail/.+/\d+', line):
                    job_urls.add(line)
                if lines_read % 50000 == 0:
                    print(f'  {lines_read} lines read, {len(job_urls)} job URLs so far...')
    except Exception as e:
        print(f'[Wayback] Error: {e}')

    print(f'[Wayback] Lines read    : {lines_read}')
    print(f'[Wayback] Job URLs found: {len(job_urls)}')
    return job_urls


def load_existing_ids(urls_file):
    existing = set()
    try:
        for url in open(urls_file).read().splitlines():
            m = re.search(r'/(\d+)(?:\?|$)', url)
            if m:
                existing.add(m.group(1))
    except FileNotFoundError:
        print(f'[!] {urls_file} not found')
    return existing


def main():
    parser = argparse.ArgumentParser(description='Fetch new job URLs from Wayback Machine')
    parser.add_argument('--urls',   required=True,               help='Existing Urls.txt to compare against')
    parser.add_argument('--output', default='wayback_new_jobs.txt', help='Output file')
    parser.add_argument('--from',   dest='date_from', default=None, help='Start date YYYYMMDD')
    parser.add_argument('--to',     dest='date_to',   default=None, help='End date YYYYMMDD')
    parser.add_argument('--limit',  type=int, default=500000,       help='Max URLs to fetch')
    args = parser.parse_args()

    # Fetch from Wayback
    job_urls = fetch_wayback(args.date_from, args.date_to, args.limit)
    if not job_urls:
        print('No URLs fetched. Exiting.')
        return

    # Load existing IDs
    print(f'\n[Filter] Loading existing IDs from {args.urls}...')
    existing = load_existing_ids(args.urls)
    print(f'[Filter] Existing job IDs: {len(existing)}')

    # Find new ones
    new_urls = []
    seen = set()
    matched = 0
    for url in job_urls:
        m = re.search(r'/(\d+)(?:\?|$)', url)
        if not m:
            continue
        job_id = m.group(1)
        if job_id in existing:
            matched += 1
        elif job_id not in seen:
            seen.add(job_id)
            new_urls.append(url)

    with open(args.output, 'w') as f:
        f.write('\n'.join(new_urls))

    print(f'''
═══════════════════════════════════════
  WAYBACK DISCOVERY COMPLETE
  Total fetched   : {len(job_urls)}
  Already known   : {matched}
  Brand new       : {len(new_urls)}
  Output          : {args.output}
═══════════════════════════════════════
''')


if __name__ == '__main__':
    main()