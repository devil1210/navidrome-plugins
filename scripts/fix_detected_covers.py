import os
import io
import requests
from pathlib import Path
from PIL import Image
from mutagen.mp4 import MP4, MP4Cover

fixes = [
    {
        "name": "Red Hot Chili Peppers - Unlimited Love",
        "folder": Path("/media/music/General/Red Hot Chili Peppers/[2022] - Unlimited Love"),
        "url": "https://cdn-images.dzcdn.net/images/cover/e68b2fbc27e04a5b936de68725955c47/1000x1000-000000-80-0-0.jpg"
    },
    {
        "name": "Koke Nuñez - El Karma",
        "folder": Path("/media/music/General/Koke Nuñez/[2026] - El Karma"),
        "url": "https://cdn-images.dzcdn.net/images/cover/157c1f7fc1e3223c22d0ecf6a1e9e461/1000x1000-000000-80-0-0.jpg"
    }
]

def apply_fixes():
    for f_info in fixes:
        folder = f_info["folder"]
        name = f_info["name"]
        url = f_info["url"]
        print(f"Fixing: {name} in {folder}...")

        if not folder.exists():
            print(f"  [ERROR] Folder not found: {folder}")
            continue

        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            print(f"  [ERROR] Failed to download {url}: {resp.status_code}")
            continue

        im = Image.open(io.BytesIO(resp.content)).convert("RGB")
        im = im.resize((1200, 1200), Image.Resampling.LANCZOS)

        # Save cover.jpg
        cover_path = folder / "cover.jpg"
        im.save(cover_path, "JPEG", quality=95, optimize=True)
        os.chmod(cover_path, 0o664)
        try:
            os.chown(cover_path, 1000, 1000)
        except Exception:
            pass

        # Prepare bytes for embedding
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=95)
        cover_bytes = buf.getvalue()

        # Embed in all .m4a
        embedded = 0
        for m4a in folder.glob("*.m4a"):
            try:
                m = MP4(str(m4a))
                m["covr"] = [MP4Cover(cover_bytes, imageformat=MP4Cover.FORMAT_JPEG)]
                m.save()
                embedded += 1
            except Exception as e:
                print(f"  [WARN] Failed to embed in {m4a.name}: {e}")

        print(f"  [OK] Saved 1200x1200 cover.jpg and embedded into {embedded} tracks for {name}")

if __name__ == "__main__":
    apply_fixes()
