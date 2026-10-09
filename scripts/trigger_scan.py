import os
import sys
from pathlib import Path
import requests
from dotenv import load_dotenv

env_path = Path("/opt/navidrome-scripts/listenbrainz_notifier/.env")
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

sys.path.insert(0, "/opt/navidrome-scripts/listenbrainz_notifier")
from modules.navidrome import NavidromeClient

nav_url = os.getenv("NAVIDROME_URL", "http://localhost:4533")
nav_user = os.getenv("NAVIDROME_USER", "admin")
nav_pass = os.getenv("NAVIDROME_PASSWORD", "")

client = NavidromeClient(nav_url, nav_user, nav_pass)
params = client._get_auth_params()
url = f"{client.base_url}/rest/startScan.view"
r = requests.get(url, params=params, timeout=10)
print(f"Status: {r.status_code}, Resp: {r.text}")
