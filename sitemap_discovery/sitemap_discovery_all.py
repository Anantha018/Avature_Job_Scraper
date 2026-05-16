"""
2_sitemap_discovery.py
======================
Step 2: Extract ALL portal paths from Urls.txt, hit /<portal>/sitemap.xml
for every portal, extract JobDetail URLs, compare against Urls.txt.

Usage:
    python 2_sitemap_discovery.py --urls Urls.txt --output sitemap_job_urls.txt
    python 2_sitemap_discovery.py --urls Urls.txt --existing-ids known_ids.txt --output sitemap_job_urls.txt
"""

import argparse
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}
TIMEOUT = 10
WORKERS = 50
NS      = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}

SKIP_PORTALS = {
    'mailRedir.php', '_linkedinApiv2', 'feed', '_cms', 'ASSET', 'portal',
    'css', 'js', 'images', 'JobDetail', 'SaveJob', 'Login', 'ApplicationMethods',
    'SearchJobs', 'SearchJobsData', 'Dashboard', 'Profile',
    'CookieNotice', 'well-known', 'sitemap.xml', 'robots.txt',
}

SKIP_COMPANIES = {
    'www.avature.net', 'careers.avature.net', 'docs.avature.net',
    'docsglobal.avature.net', 'smtp.avature.net', 'smtp1.avature.net',
    'smtp2.avature.net', 'smtp3.avature.net', 'smtp4.avature.net',
}


def extract_all_portals(urls_file):
    print(f'[1] Extracting all portals from {urls_file}...')
    raw_urls = open(urls_file).read().splitlines()
    sitemap_urls = set()

    for url in raw_urls:
        url = url.strip()
        if not url:
            continue
        try:
            url_clean = url.split('?')[0]
            proto_rest = url_clean.split('://', 1)
            if len(proto_rest) < 2:
                continue
            host_path = proto_rest[1].split('/', 1)
            sub = host_path[0].lower()
            path = host_path[1] if len(host_path) > 1 else ''
        except:
            continue

        if not sub.endswith('.avature.net') or sub in SKIP_COMPANIES:
            continue

        segs = [s for s in path.split('/') if s]
        if not segs:
            continue

        first = segs[0]
        if len(first) == 5 and first[2] == '_' and len(segs) > 1:
            locale, portal = first, segs[1]
        else:
            locale, portal = '', first

        if portal in SKIP_PORTALS or portal.startswith('.'):
            continue

        sitemap_url = (
            f'https://{sub}/{locale}/{portal}/sitemap.xml'
            if locale else
            f'https://{sub}/{portal}/sitemap.xml'
        )
        sitemap_urls.add(sitemap_url)

    print(f'[1] Total unique sitemap URLs: {len(sitemap_urls)}')
    return sitemap_urls


def fetch_sitemap(sitemap_url):
    try:
        r = requests.get(sitemap_url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return sitemap_url, []
        try:
            root = ET.fromstring(r.text)
            urls = [u.text for u in root.findall('.//sm:url/sm:loc', NS)
                   if u.text and 'JobDetail' in u.text]
        except ET.ParseError:
            urls = re.findall(r'<loc>(https://[^<]+/JobDetail/[^<]+)</loc>', r.text)
        return sitemap_url, urls
    except:
        return sitemap_url, []


def load_existing_ids(urls_file, extra_file=None):
    existing = set()
    for url in open(urls_file).read().splitlines():
        m = re.search(r'/JobDetail/.+/(\d+)', url)
        if m:
            existing.add(m.group(1))
    if extra_file:
        try:
            for url in open(extra_file).read().splitlines():
                m = re.search(r'/(\d+)(?:\?|$)', url)
                if m:
                    existing.add(m.group(1))
        except:
            pass
    return existing


def main():
    parser = argparse.ArgumentParser(description='Discover jobs via sitemaps')
    parser.add_argument('--urls',         default='Urls.txt',              help='Starter URLs file')
    parser.add_argument('--existing-ids', default=None,                    help='Extra file of already known job URLs')
    parser.add_argument('--output',       default='sitemap_job_urls.txt',  help='Output file')
    args = parser.parse_args()

    # Step 1: Extract portals
    sitemap_urls = extract_all_portals(args.urls)

    # Step 2: Fetch sitemaps
    print(f'\n[2] Fetching {len(sitemap_urls)} sitemaps...')
    all_job_urls = []
    working = 0

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(fetch_sitemap, u): u for u in sitemap_urls}
        for f in as_completed(futures):
            url, jobs = f.result()
            if jobs:
                all_job_urls.extend(jobs)
                working += 1
                sub = url.split('/')[2]
                print(f'  {sub:45s} {len(jobs):5d} jobs')

    print(f'\n[2] Sitemaps with jobs : {working}')
    print(f'[2] Raw URLs           : {len(all_job_urls)}')

    # Step 3: Filter valid
    valid = [u for u in all_job_urls if re.search(r'/JobDetail/.+/\d+', u)]
    print(f'\n[3] Valid (with ID)    : {len(valid)}')

    # Step 4: Load existing
    existing = load_existing_ids(args.urls, args.existing_ids)
    print(f'\n[4] Existing job IDs   : {len(existing)}')

    # Step 5: Find new
    new_urls = []
    seen = set()
    matched = 0
    for url in valid:
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
═══════════════════════════════════════════
  SITEMAP DISCOVERY COMPLETE
  Sitemap URLs checked  : {len(sitemap_urls)}
  Sitemaps with jobs    : {working}
  Raw URLs              : {len(all_job_urls)}
  Valid (with ID)       : {len(valid)}
  Already known         : {matched}
  Brand new jobs        : {len(new_urls)}
  Output                : {args.output}
═══════════════════════════════════════════
''')


if __name__ == '__main__':
    main()