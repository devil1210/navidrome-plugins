#!/usr/bin/env python3
"""
Picard Engine for Server-Side Post-Download Processing, Plugin Enrichment,
and Automated Library Relocation according to Picard Tagger Scripts & Configurations.
"""

import fnmatch
import html
import io
import json
import logging
import os
import re
import shutil
import time
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

_KAKASI_INST = None
_KAKASI_INITIALIZED = False

def _get_kakasi():
    global _KAKASI_INST, _KAKASI_INITIALIZED
    if not _KAKASI_INITIALIZED:
        _KAKASI_INITIALIZED = True
        try:
            import pykakasi
            _KAKASI_INST = pykakasi.kakasi()
        except Exception:
            _KAKASI_INST = None
    return _KAKASI_INST

try:
    import mutagen
    from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
except ImportError:
    mutagen = None
    MP4 = None
    MP4Cover = None
    MP4FreeForm = None

logger = logging.getLogger("listenbrainz_notifier")

# Default enabled states for Picard plugins
DEFAULT_PICARD_PLUGINS: Dict[str, bool] = {
    "auto_romanizer": True,
    "lrclib_lyrics": True,
    "lastfm": True,
    "genre_mapper": True,
    "release_type": True,
    "soundtrack": True,
    "enhanced_titles": True,
    "feat_artists": True,
    "hyphen_unicode": True,
    "deduplicator": True,
    "library_relocation": True,
    "playlist": False,
    "collect_artists": False,
}

# Unicode hyphen characters to normalize
UNICODE_HYPHENS = (
    "\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015", "\u2212", "\uff0d"
)

# Katakana -> English loanword mapping from Picard auto-romanizer plugin
KATAKANA_TO_ENGLISH: List[Tuple[str, str]] = sorted([
    ("オリジナル・サウンドトラック", "Original Soundtrack"),
    ("オリジナルサウンドトラック", "Original Soundtrack"),
    ("サウンドトラック", "Soundtrack"),
    ("オリジナル", "Original"),
    ("アニメーション", "Animation"),
    ("アニメ", "Anime"),
    ("テーマソング", "Theme Song"),
    ("テーマ", "Theme"),
    ("ベストアルバム", "Best Album"),
    ("コレクション", "Collection"),
    ("スペシャル", "Special"),
    ("エディション", "Edition"),
    ("バージョン", "Version"),
    ("ボーカル", "Vocal"),
    ("ヴォーカル", "Vocals"),
    ("インストゥルメンタル", "Instrumental"),
    ("インスト", "Inst."),
    ("アルバム", "Album"),
    ("シングル", "Single"),
    ("リミックス", "Remix"),
    ("アコースティック", "Acoustic"),
    ("ライブ", "Live"),
    ("デラックス", "Deluxe"),
    ("コンプリート", "Complete"),
    ("ミュージック", "Music"),
    ("ボーナス", "Bonus"),
    ("オープニング", "Opening"),
    ("エンディング", "Ending"),
    ("キャラクターソング", "Character Song"),
    ("キャラクター", "Character"),
    ("コンピレーション", "Compilation"),
    ("オーケストラ", "Orchestra"),
    ("ストリングス", "Strings"),
    ("ドラマ", "Drama"),
    ("ゲーム", "Game"),
    ("シアター", "Theater"),
    ("シネマ", "Cinema"),
    ("ビデオ", "Video"),
    ("ラジオ", "Radio"),
    ("デジタル", "Digital"),
    ("ステレオ", "Stereo"),
    ("リマスター", "Remaster"),
    ("リマスタード", "Remastered"),
    ("ピアノ", "Piano"),
    ("ソロ", "Solo"),
    ("ミックス", "Mix"),
], key=lambda x: len(x[0]), reverse=True)

# Acronyms to keep uppercase in enhanced titles
PRESERVE_ALLCAPS = {
    "DJ", "USA", "UK", "EP", "OST", "BPM", "VIP", "TV", "FM", "AM", "CD",
    "MC", "feat", "ft", "vs", "OP", "ED", "BGM", "Ado", "LiSA", "BABYMETAL",
    "MAN WITH A MISSION", "RADWIMPS", "BUMP OF CHICKEN", "THE ORAL CIGARETTES",
    "YOASOBI", "FLOW", "UVERworld", "SPYAIR", "ONE OK ROCK",
}

# Articles and minor words for enhanced title capitalization
LOWER_PARTICLES = {
    "the", "a", "an", "and", "but", "for", "at", "by", "from", "in", "into",
    "of", "off", "on", "onto", "out", "over", "to", "up", "with", "de", "del",
    "la", "las", "el", "los", "y", "en", "por", "con", "sin", "un", "una",
}

# Genre Mapper Replacement Pairs (matching user Picard configuration)
GENRE_MAPPER_RULES: List[Tuple[str, str]] = [
    ("*anison*", "Anime"),
    ("*anime*", "Anime"),
    ("*vocaloid*", "Vocaloid"),
    ("*j-pop*", "J-Pop"),
    ("*jpop*", "J-Pop"),
    ("*j-rock*", "J-Rock"),
    ("*jrock*", "J-Rock"),
    ("*soundtrack*", "Soundtrack"),
    ("*ost*", "Soundtrack"),
    ("*score*", "Soundtrack"),
    ("*instrumental*", "Instrumental"),
    ("*video game*", "Game Soundtrack"),
    ("*game*", "Game Soundtrack"),
    ("*pop*", "Pop"),
    ("*rock*", "Rock"),
    ("*metal*", "Metal"),
    ("*electronic*", "Electronic"),
    ("*dance*", "Dance"),
    ("*classical*", "Classical"),
]

# Prefixes/Articles for titlesort and albumsort
ARTICLES_FOR_SORT = {
    "the ", "a ", "an ", "el ", "la ", "los ", "las ", "il ", "le ", "les ",
    "un ", "una ", "unos ", "unas ", "der ", "die ", "das "
}

# Non-translation qualifier words inside parentheses or titles
LATIN_META_WORDS = {
    "feat", "ft", "cv", "tv", "ver", "version", "vs", "ep", "op", "ed",
    "from", "the", "first", "take", "live", "acoustic", "instrumental",
    "off", "vocal", "original", "mix", "remix", "edit", "size", "short",
    "full", "deluxe", "edition", "bonus", "track", "mono", "stereo", "remaster",
    "remastered", "piano", "strings", "orchestral", "arrange", "arranged", "inst"
}


class PicardEngine:
    """
    Automated Picard-compatible processor.
    Applies logic from all 11 Picard plugins and evaluates the custom
    Picard naming/relocation script for library organization.
    """

    LASTFM_API_KEY = "0a210a4a6741f2ec8f27a791b9d5d971"
    LRCLIB_URL = "https://lrclib.net/api/get"

    # In-memory caches to avoid duplicate network queries
    _lastfm_cache: Dict[str, List[str]] = {}
    _lrclib_cache: Dict[str, Optional[Dict[str, str]]] = {}

    @classmethod
    def normalize_hyphens(cls, text: str) -> str:
        """Plugin: hyphen-unicode -> normalizes unicode dashes to ASCII '-'."""
        if not text:
            return ""
        s = text
        for h in UNICODE_HYPHENS:
            s = s.replace(h, "-")
        return s

    @classmethod
    def contains_japanese(cls, text: str) -> bool:
        """Returns True if the text contains Hiragana, Katakana, or Kanji."""
        if not text:
            return False
        for char in str(text):
            if ('\u3040' <= char <= '\u309f') or ('\u30a0' <= char <= '\u30ff') or ('\u4e00' <= char <= '\u9faf'):
                return True
        return False

    @classmethod
    def already_has_latin_translation(cls, text: str) -> bool:
        """Checks if text already contains a Latin translation (e.g. 'プラネタリウム - Planetarium')."""
        if not text or not cls.contains_japanese(text):
            return False
        parts = re.split(r'\s*[\-\–\—\/]\s*', str(text))
        if len(parts) >= 2:
            jp_parts = [p for p in parts if cls.contains_japanese(p)]
            lat_parts = [p for p in parts if not cls.contains_japanese(p)]
            if jp_parts and lat_parts:
                for lat in lat_parts:
                    meaningful = [
                        w for w in re.findall(r'[a-zA-Z]{2,}', lat)
                        if w.lower() not in LATIN_META_WORDS
                    ]
                    if meaningful:
                        return True
        m = re.search(r'\(([^)]+)\)', str(text))
        if m:
            paren_content = m.group(1).strip()
            if not cls.contains_japanese(paren_content):
                meaningful = [
                    w for w in re.findall(r'[a-zA-Z]{3,}', paren_content)
                    if w.lower() not in LATIN_META_WORDS
                ]
                if len(meaningful) >= 2:
                    return True
        return False

    @classmethod
    def get_latin_part(cls, text: str) -> str:
        """Extracts the Latin/translated portion from a dual title."""
        parts = re.split(r'\s*[\-\–\—\/]\s*', str(text))
        for p in parts:
            clean_p = p.strip()
            if not cls.contains_japanese(clean_p) and re.search(r'[a-zA-Z]{2,}', clean_p):
                return clean_p
        m = re.search(r'\(([^)]+)\)', str(text))
        if m:
            paren_content = m.group(1).strip()
            if not cls.contains_japanese(paren_content) and re.search(r'[a-zA-Z]{2,}', paren_content):
                return paren_content
        return ""

    @classmethod
    def romanize_japanese(
        cls,
        text: str,
        mode: str = "auto",
        max_dual_len: int = 65,
        fallback_long: bool = True,
    ) -> str:
        """
        Plugin: auto-romanizer -> matches Picard configuration:
        - Mode 'auto': preserves original tag if already translated (e.g. 'プラネタリウム - Planetarium').
        - Builds dual title 'Kana/Kanji - Romaji'.
        - If combined length exceeds max_dual_len (default 65), uses Romaji only to prevent giant filenames.
        """
        if not text or not cls.contains_japanese(text):
            return text

        clean_text = re.sub(r'^(?:0\d{1,2}\s*[\.\-_\/:]*|\d{1,3}\s*[\.\-_\/:]+)\s*', '', str(text)).strip()

        # 1. If text already has translation
        if cls.already_has_latin_translation(clean_text):
            lat = cls.get_latin_part(clean_text)
            if mode in ("auto", "dual"):
                if fallback_long and len(clean_text) > max_dual_len and lat:
                    return lat
                return clean_text
            elif mode == "romaji":
                return lat if lat else clean_text

        # 2. Extract parenthetical trailer (e.g. ' (Instrumental)', ' (TV Size)')
        paren_match = re.match(r'^(.*?)\s*(\s*\([^)]*\)\s*)$', clean_text, re.DOTALL)
        if paren_match and cls.contains_japanese(paren_match.group(1)):
            core = paren_match.group(1).strip()
            trailer = ' ' + paren_match.group(2).strip()
        else:
            core = clean_text
            trailer = ''

        # 3. Apply Katakana loanwords
        result_core = core
        for katakana, eng in KATAKANA_TO_ENGLISH:
            if katakana in result_core:
                result_core = result_core.replace(katakana, f" {eng} ")
        result_core = re.sub(r' {2,}', ' ', result_core).strip()

        # 4. PyKakasi conversion
        romaji_core = ""
        kakasi_inst = _get_kakasi()
        if kakasi_inst:
            try:
                conversion = kakasi_inst.convert(result_core)
                romaji_parts = []
                for item in conversion:
                    if isinstance(item, dict):
                        romaji_parts.append(item.get("hepburn", item.get("orig", "")))
                    elif hasattr(item, "get"):
                        romaji_parts.append(item.get("hepburn", ""))
                    else:
                        romaji_parts.append(getattr(item, "hepburn", str(item)))
                romaji = " ".join(p for p in romaji_parts if p).strip()
                if romaji:
                    words = romaji.split()
                    cased = []
                    for idx, w in enumerate(words):
                        wl = w.lower()
                        if idx > 0 and wl in {"no", "wa", "to", "ga", "wo", "ni", "de", "kara", "made", "o", "e"}:
                            cased.append(wl)
                        else:
                            cased.append(w.capitalize())
                    romaji_core = " ".join(cased)
            except Exception as e:
                logger.debug("Kakasi conversion failed: %s", e)

        if not romaji_core:
            romaji_core = result_core

        if mode in ("auto", "dual"):
            dual_title = f"{core} - {romaji_core}{trailer}"
            if fallback_long and len(dual_title) > max_dual_len:
                return f"{romaji_core}{trailer}"
            return dual_title
        elif mode == "romaji":
            return f"{romaji_core}{trailer}"

        return clean_text

    @classmethod
    def enhance_title(cls, title: str) -> str:
        """
        Plugin: enhanced-titles -> improves capitalization, cleans spaces,
        and preserves known all-caps acronyms.
        """
        if not title:
            return ""
        s = re.sub(r"\s+", " ", title).strip()
        if cls.contains_japanese(s):
            return s

        words = s.split(" ")
        enhanced_words = []
        for i, word in enumerate(words):
            clean = re.sub(r"^[(\[\"']+|[)\]\"'.,;!?]+$", "", word)
            if clean.upper() in PRESERVE_ALLCAPS or clean in PRESERVE_ALLCAPS:
                enhanced_words.append(word)
            elif i > 0 and clean.lower() in LOWER_PARTICLES:
                enhanced_words.append(word.lower())
            else:
                enhanced_words.append(word.capitalize() if word.islower() else word)

        return " ".join(enhanced_words)

    @classmethod
    def make_sort_name(cls, name: str) -> str:
        """Generates a sort name (e.g. 'The Beatles' -> 'Beatles, The')."""
        if not name:
            return ""
        s = name.strip()
        s_lower = s.lower()
        for art in ARTICLES_FOR_SORT:
            if s_lower.startswith(art):
                main_part = s[len(art):].strip()
                art_part = s[:len(art)].strip()
                return f"{main_part}, {art_part}"
        return s

    @classmethod
    def apply_genre_mapper(cls, genres: List[str], apply_first_only: bool = True) -> List[str]:
        """
        Plugin: Genre Mapper -> maps raw genres/tags using wildcard rules:
        e.g. *anison* -> Anime, *jpop* -> J-Pop, *ost* -> Soundtrack.
        """
        if not genres:
            return []

        mapped: List[str] = []
        for g in genres:
            g_clean = g.strip()
            matched = False
            for pattern, replacement in GENRE_MAPPER_RULES:
                if fnmatch.fnmatch(g_clean.lower(), pattern.lower()):
                    if replacement not in mapped:
                        mapped.append(replacement)
                    matched = True
                    break
            if not matched:
                if g_clean not in mapped:
                    mapped.append(g_clean)

            if matched and apply_first_only:
                break

        return mapped

    @classmethod
    def apply_release_type(cls, album_name: str, is_single: bool, is_ep: bool = False) -> str:
        """
        Plugin: release-type -> appends ' EP' or ' (single)' if not already present.
        """
        if not album_name:
            return ""
        name = album_name.strip()
        lower_words = name.lower().split()
        if is_single:
            if not any(w in lower_words for w in ("single", "(single)", "sencillo")):
                return f"{name} (single)"
        elif is_ep:
            if not any(w in lower_words for w in ("ep", "e.p.")):
                return f"{name} EP"
        return name

    @classmethod
    def is_soundtrack(cls, album_name: str, genre: str = "", title: str = "") -> bool:
        """
        Plugin: soundtrack -> detects OST / Soundtrack releases from keywords.
        """
        keywords = (
            "soundtrack", "ost", "original soundtrack", "original score",
            "motion picture soundtrack", "music from", "game soundtrack", "bgm"
        )
        combined = f"{album_name} {genre} {title}".lower()
        if any(kw in combined for kw in keywords):
            return True
        if re.search(r"\bost\b", combined):
            return True
        return False

    @classmethod
    def fetch_lastfm_genres(cls, artist: str, album: str = "") -> List[str]:
        """
        Plugin: lastfm -> queries Last.fm Web API for top genre tags.
        Filtered by ignore tags ('seen live', 'favorites', '/\\d+ of \\d+ stars/').
        """
        cache_key = f"{artist.lower()}:::{album.lower()}"
        if cache_key in cls._lastfm_cache:
            return cls._lastfm_cache[cache_key]

        genres: List[str] = []
        try:
            params = {
                "method": "album.gettoptags" if album else "artist.gettoptags",
                "artist": artist,
                "api_key": cls.LASTFM_API_KEY,
                "format": "json",
            }
            if album:
                params["album"] = album
            url = f"http://ws.audioscrobbler.com/2.0/?{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(url, headers={"User-Agent": "PicardPlugin/1.0"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            tags = data.get("toptags", {}).get("tag", [])
            if isinstance(tags, dict):
                tags = [tags]

            skip_patterns = [r"^seen live$", r"^favorites$", r"^\d+ of \d+ stars$", r"^loved$", r"^albums i own$"]
            for t in tags[:8]:
                name = t.get("name", "").strip()
                if not name:
                    continue
                if any(re.match(p, name.lower()) for p in skip_patterns):
                    continue
                genres.append(name.capitalize())

            # Fallback to artist tags if album tags are empty
            if not genres and album:
                return cls.fetch_lastfm_genres(artist, album="")

        except Exception as e:
            logger.debug("Last.fm lookup failed for '%s - %s': %s", artist, album, e)

        cls._lastfm_cache[cache_key] = genres
        return genres

    @classmethod
    def fetch_lrclib_lyrics(
        cls, artist: str, title: str, album: str = "", duration: Optional[int] = None
    ) -> Optional[Dict[str, str]]:
        """
        Plugin: lrclib-lyrics -> fetches synced (Line-Level LRC) and plain lyrics.
        """
        cache_key = f"{artist.lower()}:::{title.lower()}"
        if cache_key in cls._lrclib_cache:
            return cls._lrclib_cache[cache_key]

        try:
            params: Dict[str, Any] = {
                "artist_name": artist,
                "track_name": title,
            }
            if album:
                params["album_name"] = album
            if duration and duration > 0:
                params["duration"] = duration

            url = f"{cls.LRCLIB_URL}?{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(url, headers={"User-Agent": "NavidromeNotifier/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            res: Dict[str, str] = {}
            if data.get("syncedLyrics"):
                res["synced"] = data["syncedLyrics"]
            if data.get("plainLyrics"):
                res["plain"] = data["plainLyrics"]

            if res:
                cls._lrclib_cache[cache_key] = res
                return res

        except Exception as e:
            logger.debug("LRCLIB lookup failed for '%s - %s': %s", artist, title, e)

        cls._lrclib_cache[cache_key] = None
        return None

    @classmethod
    def evaluate_naming_script(
        cls,
        artist: str,
        album: str,
        title: str,
        albumartist: Optional[str] = None,
        date: Optional[str] = None,
        tracknumber: int = 1,
        totaldiscs: int = 1,
        discnumber: int = 1,
        genre: str = "",
        isrc: str = "",
        is_compilation: bool = False,
    ) -> Tuple[str, str]:
        """
        Evaluates the user's official Picard file naming and relocation script:

        $set(_custom_genre,General)
        ...
        $if($or($eq($left($lower(%isrc%),2),jp),$rsearch(%_search_text%,j-pop|j-rock|anime|japanese|vocaloid|jpop|jrock|jpn|jpan|japan|\\bjp\\b|[\\u3040-\\u309f\\u30a0-\\u30ff\\u4e00-\\u9faf])),
          $set(_custom_genre,J-Music)
        )
        ...
        """
        target_artist = albumartist.strip() if albumartist and albumartist.strip() else artist.strip()

        # 1. Custom genre determination
        custom_genre = "General"
        search_parts = [
            genre.lower(),
            artist.lower(),
            target_artist.lower(),
            album.lower(),
            title.lower(),
            isrc.lower(),
        ]
        search_text = ";".join(p for p in search_parts if p)

        is_jp = False
        if isrc.lower().startswith("jp"):
            is_jp = True
        elif re.search(r"j-pop|j-rock|anime|japanese|vocaloid|jpop|jrock|jpn|jpan|japan|\bjp\b|yorushika|suis|ado|yoasobi|zutomayo|lisa|vaundy|kenshi yonezu|[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9faf]", search_text, re.IGNORECASE):
            is_jp = True

        if is_jp:
            custom_genre = "J-Music"

        # 2. Disc folder
        disc_folder = f"Disco {discnumber}" if (totaldiscs > 1 or discnumber > 1) else ""

        # 3. Soundtrack detection
        is_ost = 0
        album_lower = album.lower()
        genre_lower = genre.lower()
        artist_lower = target_artist.lower()

        has_soundtrack_keyword = (
            "soundtrack" in album_lower
            or "original soundtrack" in album_lower
            or bool(re.search(r"\bost\b", album_lower))
            or "soundtrack" in genre_lower
        )
        is_comp_or_various = (
            "various" in artist_lower
            or artist_lower == "soundtrack"
            or is_compilation
        )

        if has_soundtrack_keyword and is_comp_or_various:
            is_ost = 1

        # 4. Target artist adjustment
        if not target_artist or target_artist.lower() == "soundtrack":
            target_artist = artist.strip() or "Various Artists"

        # 5. Build directory path
        if re.match(r"^\[\d{4}\]\s*-\s*", album):
            year_album = album
        else:
            year_str = f"[{date[:4]}] - " if date and len(date) >= 4 else ""
            year_album = f"{year_str}{album}"

        clean_album_dir = cls.sanitize_path_component(year_album)
        clean_artist_dir = cls.sanitize_path_component(target_artist)

        if is_ost == 1:
            ost_sub = "J-Music" if custom_genre == "J-Music" else "Worldwide"
            rel_folder = f"Soundtracks/{ost_sub}/{clean_album_dir}"
        elif "various" in target_artist.lower():
            rel_folder = f"Compilaciones/{clean_album_dir}"
        elif clean_artist_dir:
            rel_folder = f"{custom_genre}/{clean_artist_dir}/{clean_album_dir}"
        else:
            rel_folder = f"Sin-Artista-del-Album/{clean_album_dir}"

        if disc_folder:
            rel_folder = f"{rel_folder}/{disc_folder}"

        # 6. Build file name: 01 - Title.m4a
        clean_title = cls.sanitize_path_component(title)
        rel_file = f"{tracknumber:02d} - {clean_title}.m4a"

        return rel_folder, rel_file

    @staticmethod
    def sanitize_path_component(name: str) -> str:
        """Sanitizes folder/file names for Windows and Linux filesystems."""
        if not name:
            return "Unknown"
        s = re.sub(r'[<>:"/\\|?*]', '_', name)
        s = re.sub(r'\s+', ' ', s)
        return s.strip('. ') or "Unknown"

    @classmethod
    def generate_m3u8_playlist(cls, album_folder: Path, tracks: List[Path]):
        """Plugin: playlist -> generates an UTF-8 .m3u8 playlist file."""
        try:
            m3u_file = album_folder / f"{album_folder.name}.m3u8"
            lines = ["#EXTM3U\n"]
            for t in sorted(tracks):
                lines.append(f"{t.name}\n")
            with open(m3u_file, "w", encoding="utf-8") as f:
                f.writelines(lines)
            logger.info("Generated M3U8 playlist: %s", m3u_file.name)
        except Exception as e:
            logger.warning("Could not create M3U8 playlist: %s", e)

    @classmethod
    def handle_deduplication(cls, target_file: Path, library_root: Path) -> bool:
        """
        Plugin: deduplicator -> if target_file already exists, moves the previous
        file to _duplicados_backup instead of leaving '(1)' duplicates.
        """
        if not target_file.exists():
            return True

        try:
            rel_path = target_file.relative_to(library_root)
            backup_file = library_root / "_duplicados_backup" / rel_path
            backup_file.parent.mkdir(parents=True, exist_ok=True)

            shutil.move(str(target_file), str(backup_file))
            logger.info("Moved existing duplicate file to backup: %s", backup_file)
            return True
        except Exception as e:
            logger.warning("Deduplicator failed for '%s': %s", target_file, e)
            return False

    @classmethod
    def fetch_itunes_release_year(cls, artist: str, album: str) -> Optional[str]:
        """Queries iTunes Search API for release year."""
        clean_artist = re.sub(r"[\(\[].*?[\)\]]", "", artist).strip()
        clean_album = re.sub(r"[\(\[].*?[\)\]]", "", album).strip()
        clean_album = re.sub(r"^(?:\[\d{4}\]\s*-\s*)", "", clean_album).strip()
        clean_album = re.sub(r"\s+\((?:single|ep)\)$", "", clean_album, flags=re.IGNORECASE).strip()
        term = f"{clean_artist} {clean_album}".strip()
        if not term:
            term = clean_album
        if not term:
            return None
        for country_param in ["", "&country=jp"]:
            url = f"https://itunes.apple.com/search?term={urllib.parse.quote(term)}&entity=album&limit=5{country_param}"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=4) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                for r in data.get("results", []):
                    rd = r.get("releaseDate")
                    if rd and len(rd) >= 4:
                        return rd[:4]
            except Exception:
                pass
        return None

    @classmethod
    def find_matching_existing_album(
        cls,
        artist_dir: Path,
        album_name: str,
        audio_files: Optional[List[Path]] = None,
    ) -> Optional[Path]:
        """
        Checks if an album folder matching album_name already exists in artist_dir
        (e.g., '[2019] - Elma' matches 'Elma' or 'Elma (single)').
        Also checks if incoming single tracks belong to an existing album in artist_dir.
        """
        if not artist_dir.exists() or not artist_dir.is_dir():
            return None

        def normalize_album_name(name: str) -> str:
            n = re.sub(r"^\d{1,3}\s*[\.\-_\/:]*\s*", "", str(name)).strip()
            n = re.sub(r"^\[\d{4}\]\s*-\s*", "", n).strip()
            n = re.sub(r"\s+\((?:single|ep)\)$", "", n, flags=re.IGNORECASE).strip()
            n = re.sub(r"[^\w\s]", "", n, flags=re.UNICODE).lower().strip()
            return re.sub(r"\s+", " ", n)

        target_norm = normalize_album_name(album_name)
        if not target_norm:
            return None

        # 1. Direct album name match against existing folders
        for folder in sorted(artist_dir.iterdir()):
            if not folder.is_dir() or folder.name.startswith("_") or folder.name.startswith("."):
                continue
            folder_norm = normalize_album_name(folder.name)
            if folder_norm and folder_norm == target_norm:
                return folder

        return None

    @classmethod
    def process_and_relocate_album(
        cls,
        temp_album_dir: Path,
        library_root: Path,
        album_name: str,
        album_artist: str,
        date: Optional[str] = None,
        is_single: bool = False,
        plugins_config: Optional[Dict[str, bool]] = None,
        uid: int = 1000,
        gid: int = 1000,
    ) -> Path:
        """
        Main entrypoint:
        1. Enriches MP4 files with enabled Picard plugins.
        2. Applies user Picard naming script to compute final destination.
        3. Relocates the album folder from downloads/ to its final library path.
        """
        cfg = dict(DEFAULT_PICARD_PLUGINS)
        if plugins_config:
            cfg.update(plugins_config)

        audio_files = sorted([
            f for f in temp_album_dir.iterdir()
            if f.is_file() and f.suffix.lower() in (".m4a", ".mp3", ".flac", ".opus")
        ])

        if not audio_files:
            return temp_album_dir

        final_album_name = album_name
        if cfg.get("hyphen_unicode", True):
            final_album_name = cls.normalize_hyphens(final_album_name)
            album_artist = cls.normalize_hyphens(album_artist)

        if cfg.get("auto_romanizer", True):
            if cls.contains_japanese(final_album_name):
                final_album_name = cls.romanize_japanese(
                    final_album_name,
                    mode="auto",
                    max_dual_len=65,
                    fallback_long=True,
                )
            if cls.contains_japanese(album_artist):
                album_artist = cls.romanize_japanese(
                    album_artist,
                    mode="auto",
                    max_dual_len=65,
                    fallback_long=True,
                )

        if cfg.get("release_type", True):
            final_album_name = cls.apply_release_type(final_album_name, is_single=is_single)

        # 1. Fetch Last.fm genres
        genres: List[str] = []
        if cfg.get("lastfm", True):
            genres = cls.fetch_lastfm_genres(album_artist, final_album_name)

        # 2. Apply Genre Mapper if enabled
        if cfg.get("genre_mapper", True) and genres:
            genres = cls.apply_genre_mapper(genres)

        genre_str = ", ".join(genres) if genres else ""

        # Process each audio file
        audio_rename_map: Dict[str, str] = {}
        for idx, audio_path in enumerate(audio_files, 1):
            try:
                if MP4 is None:
                    break
                mp4 = MP4(str(audio_path))
                if not mp4.tags:
                    continue

                raw_title = mp4.tags.get("©nam", [""])[0] if "©nam" in mp4.tags else audio_path.stem
                if cfg.get("hyphen_unicode", True):
                    raw_title = cls.normalize_hyphens(raw_title)

                if cfg.get("enhanced_titles", True):
                    raw_title = cls.enhance_title(raw_title)
                    # Tag titlesort and albumsort
                    titlesort = cls.make_sort_name(raw_title)
                    if titlesort and "sonm" not in mp4.tags:
                        mp4.tags["sonm"] = [titlesort]
                    albumsort = cls.make_sort_name(final_album_name)
                    if albumsort and "soal" not in mp4.tags:
                        mp4.tags["soal"] = [albumsort]

                processed_title = raw_title
                if cfg.get("auto_romanizer", True):
                    processed_title = cls.romanize_japanese(
                        raw_title,
                        mode="auto",
                        max_dual_len=65,
                        fallback_long=True,
                    )
                    if processed_title:
                        mp4.tags["©nam"] = [processed_title]
                        # Store original title in originaltitle if it was Japanese
                        if cls.contains_japanese(raw_title):
                            mp4.tags["----:com.apple.iTunes:originaltitle"] = [
                                MP4FreeForm(raw_title.encode("utf-8"))
                            ]

                # Update album and albumartist tags if changed
                if final_album_name:
                    mp4.tags["©alb"] = [final_album_name]
                    if cls.contains_japanese(album_name):
                        mp4.tags["----:com.apple.iTunes:originalalbum"] = [
                            MP4FreeForm(album_name.encode("utf-8"))
                        ]
                if album_artist and ("aART" not in mp4.tags or not mp4.tags["aART"]):
                    mp4.tags["aART"] = [album_artist]

                if genre_str and ("©gen" not in mp4.tags or not mp4.tags["©gen"]):
                    mp4.tags["©gen"] = [genre_str]

                # Map new filename according to Picard naming script: $num(%tracknumber%,2) - %title%.m4a
                track_num = idx
                if "trkn" in mp4.tags and mp4.tags["trkn"]:
                    track_num = mp4.tags["trkn"][0][0] or idx
                clean_title = cls.sanitize_path_component(processed_title or raw_title)
                new_audio_name = f"{track_num:02d} - {clean_title}{audio_path.suffix.lower()}"
                audio_rename_map[audio_path.name] = new_audio_name
                audio_rename_map[audio_path.with_suffix(".lrc").name] = f"{track_num:02d} - {clean_title}.lrc"

                if cfg.get("lrclib_lyrics", True):
                    track_artist = mp4.tags.get("©ART", [album_artist])[0]
                    lyrics = cls.fetch_lrclib_lyrics(track_artist, raw_title, final_album_name)
                    if lyrics:
                        # Sidecar .lrc file for Navidrome/Feishin
                        if "synced" in lyrics:
                            lrc_path = audio_path.with_suffix(".lrc")
                            if not lrc_path.exists():
                                with open(lrc_path, "w", encoding="utf-8") as lf:
                                    lf.write(lyrics["synced"])
                                logger.info("Saved sidecar .lrc lyrics for '%s'", raw_title)
                        # Embed synced or plain lyrics in audio atom
                        if "©lyr" not in mp4.tags:
                            mp4.tags["©lyr"] = [lyrics.get("synced") or lyrics.get("plain", "")]

                mp4.save()
            except Exception as e:
                logger.debug("Error enhancing metadata in %s: %s", audio_path.name, e)

        if cfg.get("playlist", False):
            cls.generate_m3u8_playlist(temp_album_dir, audio_files)

        if not cfg.get("library_relocation", True):
            return temp_album_dir

        # Ensure date is resolved for library bracket naming [YYYY] - Album
        resolved_date = date
        if not resolved_date:
            for af in audio_files:
                try:
                    if MP4 is not None and af.suffix.lower() == ".m4a":
                        m = MP4(str(af))
                        if "©day" in m.tags and m.tags["©day"]:
                            resolved_date = str(m.tags["©day"][0])[:4]
                            break
                        desc_text = ""
                        for tag_name in ["desc", "ldes", "©cmt"]:
                            if tag_name in m.tags and m.tags[tag_name]:
                                desc_text += " " + str(m.tags[tag_name][0])
                        m_year = re.search(r'(?:Released on:|℗)\s*(\d{4})', desc_text)
                        if m_year:
                            resolved_date = m_year.group(1)
                            break
                except Exception:
                    pass

        if not resolved_date:
            resolved_date = cls.fetch_itunes_release_year(album_artist, final_album_name)

        if not resolved_date:
            try:
                mtime = os.path.getmtime(str(audio_files[0]))
                resolved_date = str(time.localtime(mtime).tm_year)
            except Exception:
                resolved_date = str(time.localtime().tm_year)

        first_title = audio_files[0].stem
        rel_folder, _ = cls.evaluate_naming_script(
            artist=album_artist,
            album=final_album_name,
            title=first_title,
            albumartist=album_artist,
            date=resolved_date,
            genre=genre_str,
            is_compilation=is_single is False and "various" in album_artist.lower(),
        )

        dest_dir = library_root / rel_folder

        # Check if an existing matching album folder already exists in the artist directory
        artist_dir = dest_dir.parent
        existing_match = cls.find_matching_existing_album(
            artist_dir=artist_dir,
            album_name=final_album_name,
            audio_files=audio_files,
        )
        if existing_match:
            logger.info("Found existing matching album in library: '%s'. Merging tracks into existing folder.", existing_match.name)
            dest_dir = existing_match

        if dest_dir.resolve() == temp_album_dir.resolve():
            return temp_album_dir

        try:
            dest_dir.mkdir(parents=True, exist_ok=True)

            for item in list(temp_album_dir.iterdir()):
                dest_file_name = audio_rename_map.get(item.name, item.name)
                target_dest_file = dest_dir / dest_file_name
                if item.name == "cover.jpg" and target_dest_file.exists():
                    continue
                if cfg.get("deduplicator", True):
                    if target_dest_file.exists():
                        cls.handle_deduplication(target_dest_file, library_root)
                    # Deduplicate any existing file with the same track number prefix (e.g. '07 - Sun.m4a' vs '07 - 太陽 - Sun.m4a')
                    m_num = re.match(r"^(\d{1,2})\s*-\s*", dest_file_name)
                    if m_num and target_dest_file.suffix.lower() == ".m4a":
                        trk_prefix = f"{int(m_num.group(1)):02d} - "
                        for old_file in list(dest_dir.iterdir()):
                            if (
                                old_file.is_file()
                                and old_file.name.startswith(trk_prefix)
                                and old_file.suffix.lower() == ".m4a"
                                and old_file.name != target_dest_file.name
                            ):
                                logger.info("Deduplicando versión anterior por número de pista: '%s' -> backup", old_file.name)
                                cls.handle_deduplication(old_file, library_root)
                                old_lrc = old_file.with_suffix(".lrc")
                                if old_lrc.exists():
                                    cls.handle_deduplication(old_lrc, library_root)
                shutil.move(str(item), str(target_dest_file))

            shutil.rmtree(str(temp_album_dir), ignore_errors=True)

            try:
                if hasattr(os, "chown") and uid and gid:
                    os.chown(str(dest_dir), uid, gid)
                    curr = dest_dir.parent
                    while curr != library_root and curr.exists():
                        try:
                            os.chown(str(curr), uid, gid)
                        except Exception:
                            pass
                        curr = curr.parent
                    for root, dirs, files in os.walk(dest_dir):
                        for d in dirs:
                            os.chown(os.path.join(root, d), uid, gid)
                        for f in files:
                            os.chown(os.path.join(root, f), uid, gid)
            except Exception:
                pass

            logger.info("Successfully relocated album to library: %s", dest_dir)
            return dest_dir
        except Exception as e:
            logger.error("Failed to relocate album to '%s': %s. Keeping in '%s'", dest_dir, e, temp_album_dir)
            return temp_album_dir
