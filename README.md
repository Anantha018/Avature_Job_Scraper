# Avature Job Scraper — Engineering Write-Up

## Overview

This project scrapes job listings from Avature-hosted career pages across hundreds of companies. The goal was to maximize unique job coverage beyond the provided starter list of 587 companies and ~26k job URLs.

**Final Result: ~49,800 unique new job URLs discovered and scraped**

---


## Installation & Setup

### Prerequisites
- Python 3.10+
- Go 1.21+ (required for subfinder, amass, httpx)

### Step 1 — Install Go
Download and install from https://go.dev/dl/

Verify:
```bash
go version
```

### Step 2 — Install Go Tools (subfinder, amass, httpx)
```bash
# subfinder — passive subdomain enumeration
go install -v github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest

# amass — deep passive recon
go install -v github.com/owasp-amass/amass/v4/...@master

# httpx — HTTP probe (ProjectDiscovery version, NOT pip httpx)
go install -v github.com/projectdiscovery/httpx/cmd/httpx@latest
```

After installation, binaries land in `~/go/bin/`. Verify:
```bash
subfinder -version
amass -version
~/go/bin/httpx -version   # use full path on Windows if not in PATH
```

### Step 3 — Install Python Dependencies
```bash
pip install -r requirements.txt
playwright install chromium
```

### Step 4 — Verify Input Files
Ensure these files are present in your working directory:
```
Urls.txt          # starter pack URLs (781k lines)
```

---

## My Thought Process

### Starting Point

I was given a starter pack — `Urls.txt` with 587 companies and sample job URLs. My first instinct was to understand what we already had before trying to find more.

I parsed Urls.txt and found:
- **587 unique company subdomains** on `*.avature.net`
- **~26,284 unique job IDs** already known
- Multiple portal paths per company (`/careers`, `/jobs`, `/en_US/careers`, etc.)
- Custom domains like `emplois.bnc.ca`, `talent.ecb.europa.eu`, `jobs.aesc-group.com` pointing to Avature

The challenge was clear: find more companies, find more jobs per company, and extract clean data.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    INPUT: Urls.txt (587 companies)           │
└──────────────────────────┬──────────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
  ┌───────────────┐ ┌──────────────┐ ┌───────────────┐
  │  Subdomain    │ │   Sitemap    │ │   Wayback     │
  │  Discovery    │ │  Discovery   │ │   Machine     │
  │  (subfinder   │ │  (sitemap.xml│ │   CDX API     │
  │   + amass)    │ │  per portal) │ │               │
  └───────┬───────┘ └──────┬───────┘ └───────┬───────┘
          │                │                  │
          ▼                ▼                  ▼
  ┌─────────────────────────────────────────────────┐
  │        Deduplication against Urls.txt           │
  │        (compare job IDs, keep only new)         │
  └──────────────────────┬──────────────────────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │  all_new_job_urls   │
              │  (~49,800 URLs)     │
              └──────────┬──────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │  Playwright Scraper │
              │  (JS rendering,     │
              │   structured parse) │
              └──────────┬──────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │     jobs.jsonl      │
              │  (title, desc, loc, │
              │   date, apply_url)  │
              └─────────────────────┘
```

---

## Approach 1 — Subdomain Discovery

### The Idea
The starter list had 587 companies. I wanted to find Avature subdomains that nobody else had discovered. I'd seen a NetworkChuck video about subdomain enumeration tools and decided to use the same recon techniques security researchers use.

### Tools Used
- **subfinder** — queries 40+ passive DNS databases (Shodan, VirusTotal, Censys, SecurityTrails)
- **amass** — deeper passive recon, follows ASN/IP relationships

### Process
```bash
# Step 1: Run subdomain enumeration
subfinder -d avature.net -silent -all -o subfinder_raw.txt
amass enum -passive -d avature.net -silent -o amass_raw.txt

# Step 2: Clean results (remove sandbox, staging, smtp, UAT etc)
python discover_subdomains.py --urls Urls.txt --skip-discovery --output new_job_urls.txt --top-paths 10

# Step 3: Append unique new job URLs
python -c "
import re
existing_ids = set()
for fname in ['Urls.txt', 'all_new_job_urls.txt']:
    for url in open(fname).read().splitlines():
        m = re.search(r'/(\d+)(?:\?|$)', url)
        if m: existing_ids.add(m.group(1))
print(f'Existing IDs: {len(existing_ids)}')
new = []
for url in open('new_job_urls.txt').read().splitlines():
    url = url.strip()
    if not url: continue
    m = re.search(r'/(\d+)(?:\?|$)', url)
    if m and m.group(1) not in existing_ids:
        existing_ids.add(m.group(1))
        new.append(url)
with open('all_new_job_urls.txt', 'a') as f:
    for url in new: f.write(url + '\n')
print(f'Appended: {len(new)} truly unique URLs')
"
```

### Result
- subfinder + amass found **1,766 combined subdomains**
- DNS validated → **496 alive**
- HTTP probed → **341 clean live sites**
- Compared against starter → **56 genuinely new companies**
- Portal path tested → **12 publicly accessible new career sites**
- Net new job URLs: **~36**

### Key Insight
Most new subdomains were internal/auth-gated (406 errors even in browser). Only 12 were truly public. The real value wasn't in new companies — it was in the next approaches.

---

## Approach 2 — Sitemap Discovery (Biggest Win)

### The Idea
While exploring the HTML source of Bloomberg's career page, I noticed a `<link rel="alternate" type="application/rss+xml">` tag and a `sitemap.xml` reference. Every Avature site exposes a `/<portal>/sitemap.xml` that lists ALL active job URLs — no pagination needed.

This was the breakthrough. Instead of paginating through search results page by page, I could get every job URL in a single HTTP request.

### Key Insight: Multiple Portals Per Company
The starter list was only using ONE portal per company. But companies like DHL have 50+ portals:
- `dpdhlgroup.avature.net/de_DE/jobs/sitemap.xml`
- `dpdhlgroup.avature.net/en_US/jobs/sitemap.xml`
- `dpdhlgroup.avature.net/fr_FR/jobs/sitemap.xml`
- `dpdhlgroup.avature.net/zh_CN/jobs/sitemap.xml`
- ... and 46 more

Each locale has different jobs. The starter pack only had a fraction of these.

### Process
```bash
# Discover all job URLs via sitemaps (all portals + locale variants)
python sitemap_discovery.py --urls Urls.txt --output sitemap_job_urls.txt
```

This script:
1. Extracts EVERY unique portal path from Urls.txt (including locale variants like `de_DE/jobs`, `fr_FR/careers`)
2. Builds `sitemap.xml` URL for each portal
3. Hits all 2,590 sitemaps in parallel (50 workers)
4. Extracts all JobDetail URLs
5. Filters: must have numeric job ID, must start with `https://`
6. Deduplicates against Urls.txt

### Result
- **2,590 sitemap URLs checked**
- **499 had jobs**
- **245,555 raw job URLs collected**
- **43,671 brand new unique jobs** (not in Urls.txt)

---

## Approach 3 — Wayback Machine

### The Idea
The Internet Archive's Wayback Machine has been crawling the web since 1996. Their CDX API lets you query all archived URLs matching a pattern. I queried `*.avature.net/*/JobDetail/*` to find historical job postings — jobs that may have been removed from live sites but were indexed at some point.

### Process
```bash
# Fetch historical job URLs from Wayback Machine
python wayback_discovery.py --urls Urls.txt --output wayback_new_jobs.txt --from 20190101 --to 20231231

# Check which wayback URLs are truly unique (not in Urls.txt OR sitemaps)
python -c "
import re
existing_ids = set()
for url in open('Urls.txt').read().splitlines():
    m = re.search(r'/(\d+)(?:\?|$)', url)
    if m: existing_ids.add(m.group(1))
sitemap_ids = set()
for url in open('sitemap_job_urls.txt').read().splitlines():
    m = re.search(r'/(\d+)(?:\?|$)', url)
    if m: sitemap_ids.add(m.group(1))
wayback_urls = open('wayback_new_jobs.txt').read().splitlines()
unique_wayback = []
seen = set()
for url in wayback_urls:
    m = re.search(r'/(\d+)(?:\?|$)', url)
    if not m: continue
    job_id = m.group(1)
    if job_id not in existing_ids and job_id not in sitemap_ids and job_id not in seen:
        seen.add(job_id)
        unique_wayback.append(url)
open('wayback_truly_unique.txt', 'w').write('\n'.join(unique_wayback))
print(f'Urls.txt IDs      : {len(existing_ids)}')
print(f'Sitemap IDs       : {len(sitemap_ids)}')
print(f'Wayback total     : {len(wayback_urls)}')
print(f'Truly unique      : {len(unique_wayback)}')
"
```

### Result
- Wayback returned **6,420 job URLs**
- Already in Urls.txt: 2,723
- Already in sitemaps: 3,533
- **Brand new: 3,697 unique jobs**

---

## Combining All Sources

```bash
# Merge sitemap + wayback unique URLs
python -c "
import re
existing_ids = set()
for url in open('Urls.txt').read().splitlines():
    m = re.search(r'/(\d+)(?:\?|$)', url)
    if m: existing_ids.add(m.group(1))
seen_ids = set()
all_new = []
for fname, label in [('sitemap_job_urls.txt', 'Sitemap'), ('wayback_truly_unique.txt', 'Wayback')]:
    try:
        urls = open(fname).read().splitlines()
        count = 0
        for url in urls:
            url = url.strip()
            if not url: continue
            m = re.search(r'/(\d+)(?:\?|$)', url)
            if not m: continue
            job_id = m.group(1)
            if job_id not in existing_ids and job_id not in seen_ids:
                seen_ids.add(job_id)
                all_new.append(url)
                count += 1
        print(f'{label}: {count} unique jobs added')
    except FileNotFoundError:
        print(f'{fname} not found')
open('all_new_job_urls.txt', 'w').write('\n'.join(all_new))
print(f'Existing in Urls.txt : {len(existing_ids)}')
print(f'Total combined unique: {len(all_new)}')
"
```

**Total: ~49,800 unique new job URLs**

---

## Endpoint Discovery

### What I Found
Avature does NOT expose a public JSON API. All job data is HTML-rendered (partially server-side, partially JavaScript). Key endpoints discovered:

| Endpoint | Purpose |
|----------|---------|
| `/<portal>/SearchJobs?jobOffset=0&jobRecordsPerPage=25` | Paginated job listing |
| `/<portal>/JobDetail/<slug>/<id>` | Full job detail page |
| `/<portal>/ApplicationMethods?jobId=<id>` | Apply page |
| `/<portal>/sitemap.xml` | All job URLs in XML (goldmine) |
| `/<portal>/SearchJobs/feed/?jobRecordsPerPage=100` | RSS feed |

### Portal Path Patterns
Extracted from Urls.txt — top paths by frequency:
1. `/jobs/SearchJobs` — 323k occurrences
2. `/careers/SearchJobs` — 232k occurrences  
3. `/en_US/careers/SearchJobs` — with locale prefix
4. `/de_DE/jobs/SearchJobs` — German locale
5. 180+ more variants

---

## Scraper — Playwright-Based

### Infrastructure — EC2 + Docker
Running the scraper locally on a laptop caused high error rates due to resource constraints and network throttling. To fix this I spun up an AWS EC2 instance (`m7i-flex.large`), installed Docker, and ran the scraper inside a Playwright Docker container:

```bash
# On EC2
docker run -it --rm \
  -v ~/:/workspace \
  -w /workspace \
  mcr.microsoft.com/playwright/python:v1.59.0-jammy \
  bash

# Inside container
pip install playwright beautifulsoup4 lxml requests
python scrape_jobs_playwright.py --input all_new_job_urls.txt --output jobs.jsonl --workers 20
```

Benefits:
- **Faster** — EC2 has better network throughput than a home laptop
- **Fewer errors** — stable connection, no local resource contention
- **Non-blocking** — runs in background, detach with `tmux` and reconnect anytime

### Why Not requests + BeautifulSoup?
Initially tried `requests` + `BeautifulSoup`. Problems:
- Many sites return `406 Not Acceptable` to non-browser requests
- Job descriptions are JavaScript-rendered — empty in raw HTML
- Location and date fields only appear after JS execution

### Solution: Playwright
Playwright launches a real Chromium browser, executes JavaScript, waits for DOM content to load.

```bash
pip install playwright beautifulsoup4 lxml
playwright install chromium

# Scrape all jobs
python scrape_jobs_playwright.py --input all_new_job_urls.txt --output jobs.jsonl --workers 10  # all 49k URLs

# Resume after interruption
python scrape_jobs_playwright.py --input all_new_job_urls.txt --output jobs.jsonl --workers 10  # all 49k URLs --resume

# Limit for testing
python scrape_jobs_playwright.py --input all_new_job_urls.txt --output jobs.jsonl --limit 1000 --workers 5
```

### Data Extraction Logic
After inspecting the rendered HTML (saved with Playwright, analyzed with BeautifulSoup), I identified Avature's HTML structure:

```
Title    → <meta property="og:title"> or <h2 class="section__header__text__title">
Desc     → <div itemprop="description"> or class*="article__content--rich-text"
Location → <p><strong>Ort:/Location:</strong> City, Country</p>
Date     → <p><strong>Veröffentlichungsdatum:/Date Posted:</strong> date</p>
           or JSON-LD datePosted field
Apply    → Constructed: /<portal>/ApplicationMethods?jobId=<id>
```

### Edge Cases Handled
- **Login-gated pages** — detected by "Sign On", "KochID" text, skipped
- **Wrong page type** — checked `<meta name="avature.portal.page">` = "JobDetail"
- **`mailto:` links** — filtered, only `https://` URLs kept
- **Corrupt JSON lines** — try/except on every line
- **Rate limiting** — per-host semaphore (max 3 concurrent per domain)
- **Timeouts** — 3 retry attempts with backoff
- **Multilingual** — location/date label detection in DE/FR/ES/IT/PT

### Final Scraping Results
| Metric | Laptop | EC2 |
|--------|--------|-----|
| Total URLs | 48,248 | 48,248 |
| Success | 42,495 | 43,467 |
| Errors | 5,752 | 4,781 |
| **Clean jobs (after filtering 406s)** | **14,314** | **11,323** |

The laptop run produced more clean jobs despite lower raw success — EC2 had more 406s embedded as successes (Playwright loaded the page but received 406 content). Final output uses the laptop run: **`jobs_final.jsonl` with 14,314 clean jobs**.

### Field Coverage (jobs_final.jsonl)
| Field | Coverage |
|-------|----------|
| Title | 100% |
| Description | ~28% |
| Location | ~23% |
| Date Posted | ~16% |
| Apply URL | 100% (constructed) |

Location and date are lower because many companies don't expose them in parseable HTML — they're either in JavaScript variables or not shown at all.

---

## Cleaning the Output

After scraping, filter out failed jobs and 406 errors:

```bash
python -c "
import json
jobs = []
for line in open('jobs.jsonl', encoding='utf-8').read().splitlines():
    try:
        j = json.loads(line)
        if j.get('title') != '406 Not Acceptable' and not j.get('error'):
            jobs.append(json.dumps(j, ensure_ascii=False))
    except:
        pass
open('jobs_final.jsonl', 'w', encoding='utf-8').write('
'.join(jobs))
print(f'Final clean jobs: {len(jobs)}')
"
```

This removes:
- Jobs where the page returned `406 Not Acceptable`
- Jobs that failed to fetch entirely
- Corrupt/partial records

**Result: `jobs_final.jsonl`** — clean, ready-to-use dataset.

---

## Output Format

**`jobs.jsonl`** — one JSON object per line:

```json
{
  "job_id": "348880",
  "title": "Ausbildung Fachkraft Kurier-, Express- u. Postdienstleistungen (m/w/d) in 2027",
  "url": "https://dpdhlgroup.avature.net/de_DE/jobs/JobDetail/2027-Ausbildung-FKEP-Heilbronn/348880",
  "apply_url": "https://dpdhlgroup.avature.net/de_DE/jobs/ApplicationMethods?jobId=348880",
  "subdomain": "dpdhlgroup.avature.net",
  "location": "Heilbronn, Deutschland",
  "date_posted": "01-Apr-2026",
  "employment_type": "Vollzeit",
  "department": "",
  "description": "Wo? Heilbronn. Wann? 01.09.2027...",
  "description_html": "<div class=\"article__content article__content--rich-text\">...</div>",
  "scraped_at": "2026-05-15T12:00:00+00:00"
}
```

---

## Scripts Reference

| Script | Purpose | Key Args |
|--------|---------|----------|
| `discover_subdomains.py` | Find new companies via subfinder/amass | `--urls`, `--skip-discovery`, `--top-paths` |
| `sitemap_discovery.py` | Extract job URLs from sitemaps | `--urls`, `--output` |
| `wayback_discovery.py` | Fetch historical URLs from Wayback | `--urls`, `--output`, `--from`, `--to` |
| `scrape_jobs_playwright.py` | Scrape job detail pages | `--input`, `--output`, `--workers`, `--limit`, `--resume` |
| `check_accuracy.py` | Validate scraping quality | — |
| `check_missing.py` | Debug missing fields | — |

---

## Dataset Download

The final scraped jobs dataset is available on Google Drive:

**[jobs_final.jsonl — Download Here](https://drive.google.com/file/d/1wyxW1KzvZi2s0HP240dNBsBQmPbJxHba/view?usp=sharing)**

Contains **14,314 clean job records** with title, description, location, date posted, and apply URL.

## Coverage Summary

| Source | New Unique Jobs |
|--------|----------------|
| Subdomain discovery (subfinder/amass) | ~36 |
| Sitemap discovery (all portals + locales) | ~43,671 |
| Wayback Machine | ~4,200 |
| **Total** | **~49,800** |

Starter pack had **27,202 unique job IDs** across 781,635 URLs. We discovered **~49,800 additional unique jobs** — nearly **3x** the original dataset.

---

## What I Would Do With More Time

2. **RSS feeds** — `/<portal>/SearchJobs/feed/` returns structured XML with job data, no scraping needed
3. **Custom domain discovery** — companies like `emplois.bnc.ca`, `talent.ecb.europa.eu` use custom domains pointing to Avature. Reverse IP lookup on Avature's IP ranges would find dozens more
4. **Better location/date extraction** — site-specific selectors for the top 20 companies would dramatically improve coverage
5. **Playwright parallelism** — distribute across multiple machines for faster scraping of all 49k URLs