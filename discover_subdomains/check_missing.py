import json

jobs = []
for line in open('jobs.jsonl', encoding='utf-8').read().splitlines():
    try:
        jobs.append(json.loads(line))
    except:
        pass

success = [j for j in jobs if not j.get('error')]
no_loc  = [j for j in success if not j.get('location')]
no_date = [j for j in success if not j.get('date_posted')]

print(f'Success   : {len(success)}')
print(f'No loc    : {len(no_loc)}')
print(f'No date   : {len(no_date)}')

print('\n--- SAMPLE WITH ALL FIELDS ---')
full = [j for j in success if j.get('location') and j.get('date_posted') and j.get('description')]
if full:
    j = full[0]
    print(f'Title    : {j["title"]}')
    print(f'Location : {j["location"]}')
    print(f'Date     : {j["date_posted"]}')
    print(f'Apply    : {j["apply_url"]}')
    print(f'Desc     : {j["description"][:200]}')

print('\n--- SAMPLE MISSING LOCATION ---')
for j in no_loc[:3]:
    print(f'URL      : {j["url"]}')
    print(f'Title    : {j["title"]}')
    print()

print('\n--- SAMPLE MISSING DATE ---')
for j in no_date[:3]:
    print(f'URL      : {j["url"]}')
    print(f'Title    : {j["title"]}')
    print()