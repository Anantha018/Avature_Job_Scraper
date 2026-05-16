"""
1_discover_subdomains.py
========================
Discovers NEW Avature company career sites not in the starter list.

Pipeline:
  1. Run subfinder + amass → find all *.avature.net subdomains
  2. Clean + DNS validate
  3. Compare against Urls.txt → find truly new companies
  4. Find working portal using ONLY common paths from Urls.txt
  5. Get all job URLs from those portals (sitemap first, then pagination)
  6. Save job URLs to txt (no detail scraping)

Usage:
    python discover_subdomains.py --urls Urls.txt --output new_job_urls.txt
    python discover_subdomains.py --urls Urls.txt --output new_job_urls.txt --skip-discovery
    python discover_subdomains.py --urls Urls.txt --output new_job_urls.txt --skip-amass
"""

import argparse
import re
import shutil
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

# ── CONFIG ────────────────────────────────────────────────────────────────────

DNS_WORKERS  = 100
DNS_TIMEOUT  = 3
HTTP_WORKERS = 30
TIMEOUT      = 10
NS           = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}

BAD_SUBDOMAINS = (
    'smtp', 'iatsapp', 'sandbox', 'staging', 'uat', 'dev', 'pentest',
    'training', 'demo', 'mail', 'marketing', 'sales', 'label-studio',
    'ns-', 'broadbean', 'linkedinoneclick', 'integrations', 'techwriting',
    'rocketchat', 'labtraining', 'campusevents', 'customerservice', 'docs',
)

BAD_LIVE = (
    'integrations', 'sandbox', 'staging', 'uat', 'dev', 'pentest',
    'training', 'demo', 'smtp', 'iatsapp', 'mail', 'wildcard',
    'analytics', 'www', 'clp', 'opptly', 'facade', 'certvoutique',
    'linkedininatswidget', 'portals-', 'entitylistfeed', 'sparkhire',
    'dbgrouptest', 'mikloswiki', 'ciscostageats', 'cisivebackground',
    'eventregistration', 'contentservice', 'implementation',
    'linkedininats', 'portalsdbgroup', 'finance', 'legal', 'recruiting',
    'vendors', 'sales',
)

SKIP_PORTAL_SEGMENTS = {
    'JobDetail', 'SaveJob', 'Login', 'ApplicationMethods', 'SearchJobs',
    'SearchJobsData', 'Dashboard', 'Profile', '_linkedinApiv2', 'feed',
    '_cms', 'ASSET', 'portal', 'css', 'js', 'images', 'sitemap.xml',
    'robots.txt', 'CookieNotice', 'well-known',
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
}


# ── HELPERS ───────────────────────────────────────────────────────────────────

def section(title):
    print(f'\n{"="*55}\n  {title}\n{"="*55}')


def find_binary(name):
    found = shutil.which(name)
    if found:
        return found
    exe = name + '.exe' if sys.platform == 'win32' else name
    gobin = Path.home() / 'go' / 'bin' / exe
    return str(gobin) if gobin.exists() else None


# ── STEP 1: EXTRACT COMMON PORTAL PATHS FROM URLS.TXT ────────────────────────

def extract_common_paths(urls_file, top_n=30):
    """Extract the most common portal paths from Urls.txt."""
    counter = Counter()
    for url in open(urls_file).read().splitlines():
        url = url.strip()
        if not url:
            continue
        try:
            segs = urlparse(url).path.strip('/').split('/')
            if not segs:
                continue
            first = segs[0]
            # Handle locale prefix
            if len(first) == 5 and first[2] == '_' and len(segs) > 1:
                portal = segs[1]
            else:
                portal = first
            if portal and portal not in SKIP_PORTAL_SEGMENTS and len(portal) > 2:
                counter[portal] += 1
        except:
            pass

    common = [p for p, _ in counter.most_common(top_n)]
    print(f'[Paths] Top {top_n} common portal paths from Urls.txt:')
    for p, c in counter.most_common(top_n):
        print(f'  {c:6d}  /{p}/SearchJobs')
    return common


# ── STEP 2: SUBDOMAIN DISCOVERY ───────────────────────────────────────────────

def run_subfinder():
    binary = find_binary('subfinder')
    if not binary:
        print('[subfinder] Not found')
        return set()
    print('[subfinder] Running...')
    subprocess.run([binary, '-d', 'avature.net', '-silent', '-all', '-o', 'subfinder_raw.txt'],
                   capture_output=True, timeout=300)
    subs = set()
    try:
        for line in open('subfinder_raw.txt').read().splitlines():
            sub = line.strip().lower().replace('.avature.net', '')
            if sub and not any(sub.startswith(b) for b in BAD_SUBDOMAINS):
                subs.add(sub)
    except FileNotFoundError:
        pass
    print(f'[subfinder] {len(subs)} clean subdomains')
    return subs


def run_amass():
    binary = find_binary('amass')
    if not binary:
        print('[amass] Not found')
        return set()
    print('[amass] Loading from amass_raw.txt...')
    ansi = re.compile(r'\x1b\[[0-9;]*m')
    subs = set()
    try:
        for line in open('amass_raw.txt').read().splitlines():
            clean = ansi.sub('', line)
            for match in re.findall(r'([\w\-]+)\.avature\.net', clean):
                sub = match.strip().lower()
                if not any(sub.startswith(b) for b in BAD_SUBDOMAINS):
                    subs.add(sub)
    except FileNotFoundError:
        print('[amass] amass_raw.txt not found')
    print(f'[amass] {len(subs)} clean subdomains')
    return subs


# ── STEP 3: DNS VALIDATION ────────────────────────────────────────────────────

def dns_resolves(sub):
    try:
        socket.setdefaulttimeout(DNS_TIMEOUT)
        socket.gethostbyname(f'{sub}.avature.net')
        return sub
    except:
        return None


def dns_validate(candidates):
    print(f'[DNS] Validating {len(candidates)} subdomains...')
    valid = []
    with ThreadPoolExecutor(max_workers=DNS_WORKERS) as ex:
        futures = {ex.submit(dns_resolves, s): s for s in candidates}
        for f in as_completed(futures):
            r = f.result()
            if r:
                valid.append(r)
    print(f'[DNS] {len(valid)} resolve')
    return sorted(valid)


# ── STEP 4: COMPARE AGAINST URLS.TXT ─────────────────────────────────────────

def find_new_companies(valid_subs, urls_file):
    existing = set()
    for url in open(urls_file).read().splitlines():
        sub = urlparse(url.strip()).netloc.lower().replace('.avature.net', '')
        if sub:
            existing.add(sub)
    # Also filter bad patterns
    new = sorted(
        s for s in valid_subs
        if s not in existing and not any(b in s for b in BAD_LIVE)
    )
    print(f'[Compare] Valid: {len(valid_subs)} | In Urls.txt: {len(valid_subs)-len(new)} | New: {len(new)}')
    return new


# ── STEP 5: FIND WORKING PORTAL ───────────────────────────────────────────────

def find_portal(subdomain, common_paths):
    base = f'https://{subdomain}.avature.net'
    for portal in common_paths:
        url = f'{base}/{portal}/SearchJobs'
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
            if r.status_code == 200 and 'JobDetail' in r.text:
                return subdomain, url, portal
        except:
            pass
    return subdomain, None, None


def find_all_portals(new_companies, common_paths):
    print(f'[Portals] Testing {len(new_companies)} companies × {len(common_paths)} paths...')
    found = []
    failed = []
    with ThreadPoolExecutor(max_workers=HTTP_WORKERS) as ex:
        futures = {ex.submit(find_portal, s, common_paths): s for s in new_companies}
        for f in as_completed(futures):
            sub, url, portal = f.result()
            if url:
                found.append((sub, url, portal))
                print(f'  OK  {url}')
            else:
                failed.append(sub)
    print(f'[Portals] Working: {len(found)} | Failed: {len(failed)}')
    return found, failed


# ── STEP 6: GET JOB URLS (SITEMAP FIRST, THEN PAGINATION) ────────────────────

def fetch_sitemap(sub, portal):
    """Try to get all job URLs from sitemap."""
    url = f'https://{sub}.avature.net/{portal}/sitemap.xml'
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return []
        try:
            root = ET.fromstring(r.text)
            jobs = [u.text for u in root.findall('.//sm:url/sm:loc', NS)
                   if u.text and u.text.startswith('https://')
                   and re.search(r'/JobDetail/.+/\d+', u.text)]
        except ET.ParseError:
            jobs = re.findall(r'<loc>(https://[^<]+/JobDetail/[^<]+/\d+[^<]*)</loc>', r.text)
        return jobs
    except:
        return []


def paginate_search(search_url):
    """Paginate through SearchJobs and collect all job URLs."""
    job_urls = []
    offset = 0

    while True:
        url = f'{search_url}?jobOffset={offset}&jobRecordsPerPage=25'
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code != 200:
                break
            soup = BeautifulSoup(r.text, 'html.parser')
            links = []
            for a in soup.find_all('a', href=re.compile(r'/JobDetail/.+/\d+', re.I)):
                href = a.get('href', '')
                if not href:
                    continue
                full = urljoin(search_url, href)
                # Only keep https URLs with valid job ID — filter out mailto: etc
                if full.startswith('https://') and re.search(r'/JobDetail/.+/\d+', full):
                    links.append(full)
            if not links:
                break
            job_urls.extend(links)
            offset += 25
            time.sleep(0.3)
        except:
            break

    return list(set(job_urls))


def get_job_urls(sub, search_url, portal):
    """Try sitemap first, fall back to pagination."""
    jobs = fetch_sitemap(sub, portal)
    if jobs:
        print(f'  [sitemap] {sub}: {len(jobs)} jobs')
        return jobs
    # Fall back to pagination
    jobs = paginate_search(search_url)
    print(f'  [paginate] {sub}: {len(jobs)} jobs')
    return jobs


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--urls',           default='Urls.txt',           help='Starter Urls.txt')
    parser.add_argument('--output',         default='new_job_urls.txt',   help='Output job URLs file')
    parser.add_argument('--portals-output', default='new_portals.txt',    help='Save discovered portals')
    parser.add_argument('--skip-discovery', action='store_true',          help='Use existing subfinder/amass files')
    parser.add_argument('--skip-amass',     action='store_true',          help='Skip amass')
    parser.add_argument('--top-paths',      type=int, default=30,         help='How many top portal paths to try')
    parser.add_argument('--existing',       action='append', default=[],   help='Extra files with known job URLs to exclude (can repeat)')
    args = parser.parse_args()

    # ── Step 1: Extract common paths from Urls.txt ────────────────────────────
    section('STEP 1: EXTRACT COMMON PORTAL PATHS FROM URLS.TXT')
    common_paths = extract_common_paths(args.urls, args.top_paths)

    # ── Step 2: Subdomain discovery ───────────────────────────────────────────
    section('STEP 2: SUBDOMAIN DISCOVERY')
    if args.skip_discovery:
        subfinder_subs = run_subfinder() if find_binary('subfinder') else set()
        # Load from file directly
        try:
            subs = set()
            for line in open('subfinder_raw.txt').read().splitlines():
                sub = line.strip().lower().replace('.avature.net', '')
                if sub and not any(sub.startswith(b) for b in BAD_SUBDOMAINS):
                    subs.add(sub)
            subfinder_subs = subs
        except FileNotFoundError:
            pass
        amass_subs = set() if args.skip_amass else run_amass()
    else:
        subfinder_subs = run_subfinder()
        amass_subs     = set() if args.skip_amass else run_amass()

    all_subs = subfinder_subs | amass_subs
    print(f'Combined: {len(all_subs)} subdomains')

    # ── Step 3: DNS validate ──────────────────────────────────────────────────
    section('STEP 3: DNS VALIDATION')
    valid_subs = dns_validate(all_subs)

    # ── Step 4: Compare against Urls.txt ─────────────────────────────────────
    section('STEP 4: COMPARE AGAINST URLS.TXT')
    new_companies = find_new_companies(valid_subs, args.urls)
    print(f'New companies: {len(new_companies)}')
    for c in new_companies:
        print(f'  {c}')

    if not new_companies:
        print('No new companies found.')
        return

    with open('new_unique_sites.txt', 'w') as f:
        f.write('\n'.join(new_companies))

    # ── Step 5: Find working portal ───────────────────────────────────────────
    section('STEP 5: FIND WORKING CAREER PORTAL')
    found_portals, failed = find_all_portals(new_companies, common_paths)

    with open(args.portals_output, 'w') as f:
        for sub, url, portal in sorted(found_portals):
            f.write(f'{sub}\t{url}\n')
    print(f'Saved {len(found_portals)} portals to {args.portals_output}')

    if not found_portals:
        print('No working portals found.')
        return

    # ── Step 6: Get job URLs ──────────────────────────────────────────────────
    section('STEP 6: GET JOB URLS (sitemap → pagination)')

    # Load existing IDs from Urls.txt AND all_new_job_urls.txt
    existing_ids = set()
    for fname in [args.urls] + args.existing:
        try:
            for url in open(fname).read().splitlines():
                m = re.search(r'/(\d+)(?:\?|$)', url)
                if m:
                    existing_ids.add(m.group(1))
        except FileNotFoundError:
            pass
    print(f'[Dedup] Loaded {len(existing_ids)} existing job IDs')

    all_new_urls = []
    seen_ids = set()

    for sub, search_url, portal in found_portals:
        jobs = get_job_urls(sub, search_url, portal)
        for job_url in jobs:
            m = re.search(r'/(\d+)(?:\?|$)', job_url)
            if not m:
                continue
            job_id = m.group(1)
            if job_id not in existing_ids and job_id not in seen_ids:
                seen_ids.add(job_id)
                # Only keep https URLs
                if job_url.startswith('https://'):
                    all_new_urls.append(job_url)

    with open(args.output, 'w') as f:
        f.write('\n'.join(all_new_urls))

    print(f'''
╔══════════════════════════════════════════╗
  DISCOVERY COMPLETE
  New companies   : {len(new_companies)}
  Working portals : {len(found_portals)}
  New job URLs    : {len(all_new_urls)}
  Output          : {args.output}
╚══════════════════════════════════════════╝
''')


if __name__ == '__main__':
    main()