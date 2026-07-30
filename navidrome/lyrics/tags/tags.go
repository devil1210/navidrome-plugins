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

// ExtractPrimaryArtist extracts the main primary artist from a multi-artist or tagged string.
func ExtractPrimaryArtist(artist string) string {
	artist = strings.TrimSpace(artist)
	if artist == "" {
		return ""
	}

	// 1. Strip parenthetical voice actor or feature info e.g. "Toshiro(cv.Kishou Taniyama)" or "トシロウ（CV.谷山紀章）"
	if idx := strings.IndexAny(artist, "(（"); idx > 0 {
		cleaned := strings.TrimSpace(artist[:idx])
		if cleaned != "" {
			artist = cleaned
		}
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

// ExtractArtistCandidates returns a list of candidate artist strings to try.
func ExtractArtistCandidates(artist string) []string {
	artist = strings.TrimSpace(artist)
	if artist == "" {
		return nil
	}

	seen := make(map[string]bool)
	var candidates []string

	add := func(a string) {
		a = strings.TrimSpace(a)
		if a != "" && !seen[a] {
			seen[a] = true
			candidates = append(candidates, a)
		}
	}

	add(artist)

	// Strip parenthetical voice actor or feature info e.g. "Toshiro(cv.Kishou Taniyama)" or "トシロウ（CV.谷山紀章）"
	if idx := strings.IndexAny(artist, "(（"); idx > 0 {
		cleaned := artist[:idx]
		add(cleaned)
	}

	// Dual artist strings like "ヨルシカ - Yorushika"
	if idx := strings.Index(artist, " - "); idx > 0 {
		part1 := artist[:idx]
		part2 := artist[idx+3:]
		add(part1)
		add(part2)
	}

	primary := ExtractPrimaryArtist(artist)
	add(primary)

	return candidates
}

// ExtractTitleCandidates returns a list of candidate title strings to try.
func ExtractTitleCandidates(title string) []string {
	title = strings.TrimSpace(title)
	if title == "" {
		return nil
	}

	seen := make(map[string]bool)
	var candidates []string

	add := func(t string) {
		t = strings.TrimSpace(t)
		if t != "" && !seen[t] {
			seen[t] = true
			candidates = append(candidates, t)
		}
	}

	add(title)

	// Dual titles like "太陽 - Taiyou" or "新世界 - Shinsekai"
	if idx := strings.Index(title, " - "); idx > 0 {
		part1 := title[:idx]
		part2 := title[idx+3:]
		add(part1)
		add(part2)
	}

	// Titles with parentheses like "Carry On (Album ver.)" or "サムライハート (Some Like It Hot)"
	if idx := strings.IndexAny(title, "(（"); idx > 0 {
		cleaned := title[:idx]
		add(cleaned)
	}

	return candidates
}
