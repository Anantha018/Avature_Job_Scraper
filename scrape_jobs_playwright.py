"""
scrape_jobs_playwright.py
=========================
Scrapes Avature job detail pages using Playwright (real browser).
Handles JS-rendered content, extracts all fields accurately.

Usage:
    python scrape_jobs_playwright.py --input all_new_job_urls.txt --output jobs.jsonl --limit 1000
    python scrape_jobs_playwright.py --input all_new_job_urls.txt --output jobs.jsonl --resume
    python scrape_jobs_playwright.py --input all_new_job_urls.txt --output jobs.jsonl --workers 5

Requirements:
    pip install playwright beautifulsoup4 lxml
    playwright install chromium
"""

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout


# ── PARSE JOB PAGE ────────────────────────────────────────────────────────────

def parse(html, url):
    soup = BeautifulSoup(html, 'lxml')

    # ── Detect login/gated/wrong pages ───────────────────────────────────────
    page_text = soup.get_text(strip=True).lower()
    login_signals = ['sign on', 'log in with password', 'kochid', 'please log in',
                     'login required', 'you must be logged in', 'sign in to continue']
    if any(s in page_text[:500] for s in login_signals):
        return None  # login-gated, skip

    # Check we're on an actual job detail page (not homepage/search)
    avature_page = soup.find('meta', attrs={'name': 'avature.portal.page'})
    if avature_page:
        page_type = avature_page.get('content', '')
        if page_type != 'JobDetail':
            return None  # redirected to non-job page

    # ── JSON-LD (richest source) ──────────────────────────────────────────────
    ld = {}
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or '')
            if isinstance(data, list):
                data = data[0]
            if data.get('@type') == 'JobPosting':
                ld = data
                break
        except:
            pass

    # ── Title ─────────────────────────────────────────────────────────────────
    title = ld.get('title', '')
    if not title:
        og = soup.find('meta', property='og:title')
        if og:
            title = og.get('content', '').strip()
    if not title:
        h1 = soup.find('h1')
        if h1:
            title = h1.get_text(strip=True)
    if not title:
        # Avature uses h2.section__header__text__title for job title
        el = soup.find('h2', class_=re.compile(r'section.*title|title.*section', re.I))
        if el:
            t = el.get_text(strip=True)
            if len(t) > 5:
                title = t
    if not title:
        el = soup.find(class_=re.compile(r'job.?title|title.?job', re.I))
        if el:
            t = el.get_text(strip=True)
            if len(t) > 5:
                title = t
    if not title:
        t = soup.find('title')
        if t:
            title = t.get_text(strip=True).split('-')[0].strip()
    if not title:
        # Extract from URL slug - clean up location prefix if present
        m = re.search(r'/JobDetail/([^/]+)/\d+', url)
        if m:
            slug = m.group(1).replace('-', ' ').strip()
            title = slug

    # ── Description ───────────────────────────────────────────────────────────
    desc_html = ld.get('description', '')
    if not desc_html:
        # Avature uses itemprop="description" — most reliable
        el = soup.find(attrs={'itemprop': 'description'})
        if el and len(el.get_text(strip=True)) > 50:
            desc_html = str(el)
    if not desc_html:
        for sel in [
            {'class': re.compile(r'article__content.*rich.text|rich.text.*article__content', re.I)},
            {'class': re.compile(r'job.?description|jobDescription', re.I)},
            {'id':    re.compile(r'description|jobDesc', re.I)},
            {'class': re.compile(r'description.?content|content.?description', re.I)},
            {'class': re.compile(r'section.?description|description.?section', re.I)},
            {'class': re.compile(r'posting.?body|body.?posting', re.I)},
            {'class': re.compile(r'requisition|requirements|responsibilities', re.I)},
            {'id':    re.compile(r'job.?detail|posting|requisition', re.I)},
        ]:
            el = soup.find(attrs=sel)
            if el and len(el.get_text(strip=True)) > 50:
                desc_html = str(el)
                break
    # Fallback: find best content block (not nav/header/footer)
    if not desc_html:
        skip_tags = {'nav', 'header', 'footer', 'script', 'style'}
        candidates = [
            el for el in soup.find_all(['div', 'section', 'article', 'main'])
            if el.name not in skip_tags
            and not el.find_parent(['nav', 'header', 'footer'])
            and len(el.get_text(strip=True)) > 200
        ]
        if candidates:
            best = max(candidates, key=lambda x: len(x.get_text(strip=True)))
            desc_html = str(best)

    desc_text = re.sub(r'\s+', ' ',
        BeautifulSoup(desc_html, 'lxml').get_text(' ', strip=True)
    ).strip() if desc_html else ''

    # ── Location ──────────────────────────────────────────────────────────────
    location = ''
    loc_data = ld.get('jobLocation', {})
    if isinstance(loc_data, list):
        loc_data = loc_data[0] if loc_data else {}
    if isinstance(loc_data, dict):
        addr = loc_data.get('address', {})
        parts = [
            addr.get('addressLocality', ''),
            addr.get('addressRegion', ''),
            addr.get('addressCountry', ''),
        ]
        location = ', '.join(p for p in parts if p)
    if not location:
        # Avature uses <p><strong>Ort:</strong> City</p> or <strong>Location:</strong>
        for strong in soup.find_all('strong'):
            text = strong.get_text(strip=True).lower()
            if text in ('ort:', 'location:', 'lieu:', 'ubicación:', 'localização:', 'luogo:'):
                parent = strong.parent
                if parent:
                    full = parent.get_text(strip=True)
                    # Remove the label prefix
                    location = re.sub(r'^(?:ort|location|lieu|ubicaci[oó]n|localiza[cç][aã]o|luogo):\s*', '', full, flags=re.I).strip()
                    if location and len(location) < 150:
                        break
    if not location:
        for sel in [
            {'class': 'list-item-location'},
            {'class': re.compile(r'^location$', re.I)},
            {'class': re.compile(r'job.?location|location.?job', re.I)},
            {'class': re.compile(r'article.+location|location.+text', re.I)},
        ]:
            el = soup.find(attrs=sel)
            if el:
                loc_text = el.get_text(strip=True)
                if loc_text and len(loc_text) < 150:
                    location = loc_text
                    break
    if not location:
        # Extract location from URL slug using known country patterns
        loc_m = re.search(r'/JobDetail/([^/]+)/\d+', url)
        if loc_m:
            slug = loc_m.group(1)
            known_countries = (
                r'(?:United-States|United-Kingdom|Great-Britain|Germany|France|'
                r'Australia|Canada|Netherlands|Belgium|Italy|Spain|Poland|'
                r'Switzerland|Austria|India|Japan|China|Brazil|Mexico|Ireland|'
                r'Singapore|Denmark|Sweden|Norway|Finland|Portugal|Romania|'
                r'New-Zealand|South-Africa|Saudi-Arabia|United-Arab-Emirates)'
            )
            country_match = re.search(known_countries, slug)
            if country_match:
                loc_slug = slug[:country_match.end()]
                # Clean up repeated words e.g. "New-York-New-York" -> "New York"
                parts = loc_slug.split('-')
                seen = []
                for p in parts:
                    if p not in seen:
                        seen.append(p)
                location = ', '.join(seen)

    # ── Date Posted ───────────────────────────────────────────────────────────
    date_posted = ld.get('datePosted', '')
    if not date_posted:
        t = soup.find('time', attrs={'datetime': True})
        if t:
            date_posted = t.get('datetime', '')
    if not date_posted:
        # Avature uses <p><strong>Veröffentlichungsdatum:</strong> date</p>
        date_labels = ('veröffentlichungsdatum:', 'date posted:', 'date de publication:',
                       'fecha de publicación:', 'data di pubblicazione:', 'posting date:',
                       'published:', 'posted:', 'posted on:')
        for strong in soup.find_all('strong'):
            if strong.get_text(strip=True).lower() in date_labels:
                parent = strong.parent
                if parent:
                    full = parent.get_text(strip=True)
                    date_text = re.sub(r'^[^:]+:\s*', '', full).strip()
                    if date_text and len(date_text) < 30:
                        date_posted = date_text
                        break
    if not date_posted:
        el = soup.find(attrs={'class': re.compile(r'date.?post|post.?date', re.I)})
        if el:
            date_posted = el.get_text(strip=True)

    # ── Employment Type ───────────────────────────────────────────────────────
    employment_type = ld.get('employmentType', '')
    if not employment_type:
        el = soup.find(attrs={'class': re.compile(r'employ|work.?type|job.?type', re.I)})
        if el:
            employment_type = el.get_text(strip=True)

    # ── Department ────────────────────────────────────────────────────────────
    department = ''
    el = soup.find(attrs={'class': re.compile(r'department|category|function|division', re.I)})
    if el:
        department = el.get_text(strip=True)

    # ── Apply URL ─────────────────────────────────────────────────────────────
    apply_url = url
    m = re.search(r'(https://[^/]+(?:/[^/]+)*)/JobDetail/.+?/(\d+)(?:\?|$)', url)
    if m:
        apply_url = f'{m.group(1)}/ApplicationMethods?jobId={m.group(2)}'

    # ── Job ID + Subdomain ────────────────────────────────────────────────────
    m = re.search(r'/(\d+)(?:\?|$)', url)
    job_id = m.group(1) if m else ''
    subdomain = url.split('/')[2] if url.startswith('http') else ''

    return {
        'job_id':           job_id,
        'title':            title,
        'url':              url,
        'apply_url':        apply_url,
        'subdomain':        subdomain,
        'location':         location,
        'date_posted':      date_posted,
        'employment_type':  employment_type,
        'department':       department,
        'description':      desc_text,
        'description_html': desc_html,
        'scraped_at':       datetime.now(timezone.utc).isoformat(),
    }


# ── PLAYWRIGHT SCRAPER ────────────────────────────────────────────────────────

async def scrape_batch(urls, output_file, semaphore, browser, stats):
    """Scrape a batch of URLs using shared browser."""
    async def scrape_one(url):
        async with semaphore:
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                locale='en-US',
            )
            page = await context.new_page()
            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=20000)
                # Wait for job content
                try:
                    await page.wait_for_selector(
                        '[itemprop="description"], .article__content--rich-text, h2, main',
                        timeout=5000
                    )
                except PlaywrightTimeout:
                    pass
                html = await page.content()
                job = parse(html, url)
                if job is None:
                    stats['errors'] += 1
                    m = re.search(r'/(\d+)(?:\?|$)', url)
                    return {
                        'job_id': m.group(1) if m else '',
                        'url': url,
                        'subdomain': url.split('/')[2] if url.startswith('http') else '',
                        'scraped_at': datetime.now(timezone.utc).isoformat(),
                        'error': 'login_required',
                    }
                stats['success'] += 1
                return job

            except PlaywrightTimeout:
                stats['errors'] += 1
                m = re.search(r'/(\d+)(?:\?|$)', url)
                return {
                    'job_id': m.group(1) if m else '',
                    'url': url,
                    'subdomain': url.split('/')[2] if url.startswith('http') else '',
                    'scraped_at': datetime.now(timezone.utc).isoformat(),
                    'error': 'timeout',
                }
            except Exception as e:
                stats['errors'] += 1
                m = re.search(r'/(\d+)(?:\?|$)', url)
                return {
                    'job_id': m.group(1) if m else '',
                    'url': url,
                    'subdomain': url.split('/')[2] if url.startswith('http') else '',
                    'scraped_at': datetime.now(timezone.utc).isoformat(),
                    'error': str(e)[:100],
                }
            finally:
                await page.close()
                await context.close()

    tasks = [scrape_one(url) for url in urls]
    results = await asyncio.gather(*tasks)

    with open(output_file, 'a', encoding='utf-8') as f:
        for job in results:
            f.write(json.dumps(job, ensure_ascii=False) + '\n')

    return results


async def run(args):
    # Load URLs
    urls = [u.strip() for u in open(args.input, encoding='utf-8').read().splitlines()
            if u.strip() and u.strip().startswith('https://')]
    print(f'Loaded {len(urls)} URLs')

    # Resume
    if args.resume:
        try:
            done = set()
            for line in open(args.output, encoding='utf-8').read().splitlines():
                try:
                    j = json.loads(line)
                    if j.get('job_id'):
                        done.add(j['job_id'])
                except:
                    pass
            before = len(urls)
            urls = [u for u in urls if not (
                re.search(r'/(\d+)(?:\?|$)', u) and
                re.search(r'/(\d+)(?:\?|$)', u).group(1) in done
            )]
            print(f'Resume: skipping {before - len(urls)}, {len(urls)} remaining')
        except FileNotFoundError:
            pass

    # Limit
    if args.limit > 0:
        urls = urls[:args.limit]
        print(f'Limiting to first {args.limit} URLs')

    total = len(urls)
    stats = {'success': 0, 'errors': 0}
    semaphore = asyncio.Semaphore(args.workers)

    print(f'Scraping {total} jobs with {args.workers} concurrent browsers...\n')

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # Process in batches of 50 for progress reporting
        batch_size = 50
        for i in range(0, total, batch_size):
            batch = urls[i:i + batch_size]
            await scrape_batch(batch, args.output, semaphore, browser, stats)
            done = i + len(batch)
            print(f'  {done}/{total} | success: {stats["success"]} | errors: {stats["errors"]}')

        await browser.close()

    print(f'''
═══════════════════════════════════════
  SCRAPING COMPLETE
  Total   : {total}
  Success : {stats["success"]}
  Errors  : {stats["errors"]}
  Output  : {args.output}
═══════════════════════════════════════
''')


def main():
    parser = argparse.ArgumentParser(description='Scrape Avature jobs using Playwright')
    parser.add_argument('--input',   default='all_new_job_urls.txt', help='Input URLs file')
    parser.add_argument('--output',  default='jobs.jsonl',           help='Output JSONL file')
    parser.add_argument('--limit',   type=int, default=0,            help='Max jobs (0=all)')
    parser.add_argument('--workers', type=int, default=5,            help='Concurrent browsers')
    parser.add_argument('--resume',  action='store_true',            help='Skip already scraped')
    args = parser.parse_args()

    asyncio.run(run(args))


if __name__ == '__main__':
    main()