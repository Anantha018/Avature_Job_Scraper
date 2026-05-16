import requests, json, re
from bs4 import BeautifulSoup

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

url = 'https://dpdhlgroup.avature.net/de_DE/jobs/JobDetail/2027-Ausbildung-FKEP-Heilbronn/348880'
r = requests.get(url, headers=HEADERS, timeout=15)
print(f'Status: {r.status_code}')
soup = BeautifulSoup(r.text, 'lxml')

# Print ALL div classes that have substantial text
print('\n=== DIV CLASSES WITH TEXT ===')
for div in soup.find_all(['div', 'section', 'article']):
    cls = div.get('class', [])
    text = div.get_text(strip=True)
    if cls and 100 < len(text) < 2000:
        print(f'  {cls}: {text[:100]}')

print('\n=== ALL CLASSES CONTAINING "desc" ===')
for el in soup.find_all(class_=re.compile(r'desc', re.I)):
    print(f'  {el.get("class")}: {el.get_text(strip=True)[:100]}')

print('\n=== MAIN TAG ===')
main = soup.find('main')
if main:
    print(main.get_text(separator=' ', strip=True)[:500])