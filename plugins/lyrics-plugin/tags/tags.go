package tags

import (
	"strings"
)

// TagExtractor extracts lyrics embedded in audio file metadata tags (e.g., USLT, SYLT, LYRICS).
type TagExtractor struct{}

func NewTagExtractor() *TagExtractor {
	return &TagExtractor{}
}

// ExtractFromMetadata attempts to extract lyrics from standard audio tags.
func (e *TagExtractor) ExtractFromMetadata(rawTags map[string]string) (string, bool) {
	for _, key := range []string{"LYRICS", "UNSYNCEDLYRICS", "USLT", "SYLT", "WM/Lyrics"} {
		if val, ok := rawTags[key]; ok && strings.TrimSpace(val) != "" {
			return strings.TrimSpace(val), true
		}
		if val, ok := rawTags[strings.ToLower(key)]; ok && strings.TrimSpace(val) != "" {
			return strings.TrimSpace(val), true
		}
	}
	return "", false
}

// ExtractOriginalMetadata checks rawTags for ORIGINALTITLE, ORIGINALARTIST, and ORIGINALALBUM tags.
func (e *TagExtractor) ExtractOriginalMetadata(rawTags map[string]string) (title, artist, album string) {
	if len(rawTags) == 0 {
		return "", "", ""
	}

	for _, key := range []string{"ORIGINALTITLE", "originaltitle", "_original_title", "ORIGINAL_TITLE"} {
		if val, ok := rawTags[key]; ok && strings.TrimSpace(val) != "" {
			title = strings.TrimSpace(val)
			break
		}
	}

	for _, key := range []string{"ORIGINALARTIST", "originalartist", "_original_artist", "ORIGINAL_ARTIST"} {
		if val, ok := rawTags[key]; ok && strings.TrimSpace(val) != "" {
			artist = strings.TrimSpace(val)
			break
		}
	}

	for _, key := range []string{"ORIGINALALBUM", "originalalbum", "_original_album", "ORIGINAL_ALBUM"} {
		if val, ok := rawTags[key]; ok && strings.TrimSpace(val) != "" {
			album = strings.TrimSpace(val)
			break
		}
	}

	return title, artist, album
}

// ExtractPrimaryArtist extracts the main primary artist from a multi-artist string.
func ExtractPrimaryArtist(artist string) string {
	artist = strings.TrimSpace(artist)
	if artist == "" {
		return ""
	}

	delimiters := []string{
		" feat. ", " FEAT. ", " Feat. ",
		" ft. ", " FT. ", " Ft. ",
		" featuring ", " FEATURING ",
		" and ", " AND ",
		" with ", " WITH ",
		" vs. ", " VS. ", " vs ",
		" & ", " / ", " ; ", ", ",
	}

	primary := artist
	for _, del := range delimiters {
		if idx := strings.Index(primary, del); idx > 0 {
			primary = primary[:idx]
		}
	}

	return strings.TrimSpace(primary)
}
