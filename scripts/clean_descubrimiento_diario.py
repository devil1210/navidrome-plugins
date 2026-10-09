#!/usr/bin/env python3
import os
import sys
import json
from pathlib import Path

sys.path.insert(0, "/opt/navidrome-scripts/listenbrainz_notifier")
from modules.navidrome import NavidromeClient
from dotenv import load_dotenv

load_dotenv("/opt/navidrome-scripts/listenbrainz_notifier/.env")
client = NavidromeClient(os.getenv("NAVIDROME_URL"), os.getenv("NAVIDROME_USER"), os.getenv("NAVIDROME_PASSWORD"))

playlist_id = "46cnVJuUELdI3X5g69ZL21"

details = client.get_playlist_details(playlist_id)
entries = details.get("entry", [])
if isinstance(entries, dict):
    entries = [entries]

print(f"Total current entries in Descubrimiento Diario: {len(entries)}")

# Keep the first 37 legitimate tracks
legitimate_entries = entries[:37]
removed_entries = entries[37:]

print(f"\n--- KEEPING {len(legitimate_entries)} LEGITIMATE TRACKS ---")
for idx, e in enumerate(legitimate_entries, 1):
    print(f"  {idx:2d}. {e.get('artist')} - {e.get('title')}")

print(f"\n--- REMOVING {len(removed_entries)} FALSE POSITIVE / AUTO-INJECTED TRACKS ---")
for idx, e in enumerate(removed_entries, 1):
    print(f"  {idx:2d}. {e.get('artist')} - {e.get('title')} [{e.get('album')}]")

clean_song_ids = [e["id"] for e in legitimate_entries if e.get("id")]

ok = client.set_playlist_tracks(playlist_id, clean_song_ids)
if ok:
    print(f"\n✅ Successfully updated playlist '{details.get('name')}' with {len(clean_song_ids)} songs!")
    # Verify
    updated_details = client.get_playlist_details(playlist_id)
    new_entries = updated_details.get("entry", [])
    if isinstance(new_entries, dict):
        new_entries = [new_entries]
    print(f"Verified count in Navidrome: {len(new_entries)} songs.")
else:
    print(f"\n❌ Error updating playlist tracks via Navidrome API.")
