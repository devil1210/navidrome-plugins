#!/usr/bin/env python3
import os
import sys
import json
import requests
import re
from pathlib import Path

sys.path.insert(0, "/opt/navidrome-scripts/listenbrainz_notifier")
from modules.navidrome import NavidromeClient
from dotenv import load_dotenv

load_dotenv("/opt/navidrome-scripts/listenbrainz_notifier/.env")
client = NavidromeClient(os.getenv("NAVIDROME_URL"), os.getenv("NAVIDROME_USER"), os.getenv("NAVIDROME_PASSWORD"))

lbz_playlists = {
    "The Guy She Was Interested In Wasn't A Guy At All": "0d9612a0-f729-4b1c-824a-907191336554",
    "Descubrimiento de la Semana": "86d86bca-e647-4de8-858b-3f105dfbdbee",
    "ListenBrainz Daily Jams": "2b51972c-d200-4200-b116-0aaec81d2a95",
    "ListenBrainz Weekly Jams": "26d3b9f9-6634-4925-8929-9227266029b1",
}

def norm(s):
    if not s:
        return ""
    clean = re.sub(r"\s*[\(\[].*?[\)\]]", "", s).strip()
    clean = re.sub(r"^\d+[\s\.\-_]+", "", clean).strip()
    clean = clean.replace("’", "'").replace("`", "'")
    return re.sub(r"[^\w\s]", "", clean.lower()).strip()

all_pls = client.get_playlists()
pl_map = {p["name"]: p for p in all_pls}

# 1. Audit the 3 Direct ListenBrainz Playlists
for pl_name, mbid in lbz_playlists.items():
    print(f"\n" + "="*50)
    print(f"AUDIT: {pl_name}")
    print("="*50)
    
    r = requests.get(f"https://api.listenbrainz.org/1/playlist/{mbid}", timeout=15)
    if r.status_code != 200:
        print(f"Failed to fetch LBZ playlist {mbid}: {r.status_code}")
        continue
    lbz_data = r.json()
    lbz_tracks = lbz_data.get("playlist", {}).get("track", [])
    print(f"ListenBrainz official tracklist: {len(lbz_tracks)} tracks")
    
    nav_pl = pl_map.get(pl_name)
    if not nav_pl:
        print("Playlist not found in Navidrome!")
        continue
    nav_details = client.get_playlist_details(nav_pl["id"])
    nav_entries = nav_details.get("entry", [])
    if isinstance(nav_entries, dict):
        nav_entries = [nav_entries]
    print(f"Navidrome current tracklist: {len(nav_entries)} tracks")
    
    mismatches = []
    matched = 0
    unmatched_in_lbz = []
    
    for idx, ne in enumerate(nav_entries, 1):
        n_title = ne.get("title", "")
        n_artist = ne.get("artist", "")
        n_album = ne.get("album", "")
        norm_nt = norm(n_title)
        norm_na = norm(n_artist)
        
        found_exact = False
        found_title_only = []
        for lt in lbz_tracks:
            l_title = lt.get("title", "")
            l_creator = lt.get("creator", "")
            norm_lt = norm(l_title)
            norm_lc = norm(l_creator)
            
            t_match = (
                norm_nt == norm_lt 
                or (len(norm_nt) >= 4 and (norm_nt in norm_lt or norm_lt in norm_nt))
                or norm_nt.replace(" ", "") == norm_lt.replace(" ", "")
            )
            
            a_match = (
                norm_na == norm_lc 
                or (len(norm_na) >= 3 and (norm_na in norm_lc or norm_lc in norm_na))
                or any(t in norm_lc for t in norm_na.split() if len(t) >= 3)
                or any(t in norm_na for t in norm_lc.split() if len(t) >= 3)
            )
            
            if t_match and a_match:
                found_exact = True
                break
            elif t_match:
                found_title_only.append(lt)
                
        if found_exact:
            matched += 1
        elif found_title_only:
            for ft in found_title_only:
                mismatches.append({
                    "pos": idx,
                    "nav_id": ne.get("id"),
                    "nav_title": n_title,
                    "nav_artist": n_artist,
                    "nav_album": n_album,
                    "lbz_title": ft.get("title"),
                    "lbz_creator": ft.get("creator"),
                })
        else:
            unmatched_in_lbz.append({
                "pos": idx,
                "nav_id": ne.get("id"),
                "nav_title": n_title,
                "nav_artist": n_artist,
                "nav_album": n_album,
            })
            
    print(f"✓ Correctly matched (Title + Artist): {matched}")
    if mismatches:
        print(f"⚠️ POTENTIAL MISMATCHES (Title match, Artist differ): {len(mismatches)}")
        for m in mismatches:
            print(f"  [Pos #{m['pos']}] Navidrome: '{m['nav_title']}' by '{m['nav_artist']}' (Album: '{m['nav_album']}')")
            print(f"               LBZ wanted: '{m['lbz_title']}' by '{m['lbz_creator']}'")
    else:
        print("✓ No title-only mismatches found!")
        
    if unmatched_in_lbz:
        print(f"❓ In Navidrome but not in LBZ playlist ({len(unmatched_in_lbz)}):")
        for u in unmatched_in_lbz:
            print(f"  [Pos #{u['pos']}] '{u['nav_title']}' by '{u['nav_artist']}' (Album: '{u['nav_album']}')")

# 2. Audit Descubrimiento Diario
print(f"\n" + "="*50)
print(f"AUDIT: Descubrimiento Diario")
print("="*50)
dd_pl = pl_map.get("Descubrimiento Diario")
if dd_pl:
    dd_details = client.get_playlist_details(dd_pl["id"])
    dd_entries = dd_details.get("entry", [])
    if isinstance(dd_entries, dict):
        dd_entries = [dd_entries]
    print(f"Descubrimiento Diario has {len(dd_entries)} tracks in Navidrome:")
    print(f"Comment: {dd_pl.get('comment')}")
    
    # Check CF recommendations from ListenBrainz
    lbz_token = "bc0a951d-fb78-410c-946b-86e282dd4e48"
    headers = {"Authorization": f"Token {lbz_token}"}
    cf_url = "https://api.listenbrainz.org/1/cf/recommendation/user/devil_1210/recording"
    r_cf = requests.get(cf_url, headers=headers, timeout=15)
    cf_mbids = set()
    if r_cf.status_code == 200:
        cf_data = r_cf.json()
        cf_mbids = {item["recording_mbid"] for item in cf_data.get("payload", {}).get("mbids", [])}
        print(f"Fetched {len(cf_mbids)} CF recommendations from ListenBrainz")
        
    for idx, e in enumerate(dd_entries, 1):
        print(f"  {idx:2d}. {e.get('artist')} - {e.get('title')} [{e.get('album')}] (ID: {e.get('id')})")
