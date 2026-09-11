import requests, re
url = 'https://www.youtube.com/@kokecantante'
r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}, allow_redirects=True)
print('Status:', r.status_code)
print('Final URL:', r.url)

for p in [
    r'channel_id=([a-zA-Z0-9_-]+)',
    r'"externalId":"([a-zA-Z0-9_-]+)"',
    r'<meta itemprop="channelId" content="([a-zA-Z0-9_-]+)">',
    r'"canonicalBaseUrl":"/channel/([a-zA-Z0-9_-]+)"'
]:
    m = re.search(p, r.text)
    if m:
        print(f'{p} -> {m.group(1)}')
