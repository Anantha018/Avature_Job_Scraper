import json

jobs = []
for line in open('jobs.jsonl', encoding='utf-8').read().splitlines():
    try:
        jobs.append(json.loads(line))
    except:
        pass

success = [j for j in jobs if not j.get('error')]
print(f'Total     : {len(jobs)}')
print(f'Success   : {len(success)}')
print(f'Has title : {sum(1 for j in success if j.get("title"))}')
print(f'Has desc  : {sum(1 for j in success if j.get("description"))}')
print(f'Has loc   : {sum(1 for j in success if j.get("location"))}')
print(f'Has date  : {sum(1 for j in success if j.get("date_posted"))}')
print()
print('Sample job:')
sample = next((j for j in success if j.get('location')), success[0])
print(f'  Title    : {sample["title"]}')
print(f'  Location : {sample["location"]}')
print(f'  Date     : {sample["date_posted"]}')
print(f'  Apply    : {sample["apply_url"]}')
print(f'  Desc     : {sample["description"][:150]}...')