package providers

import (
	"encoding/json"
	"fmt"
	"net/url"
	"regexp"
	"strings"

	"github.com/navidrome/navidrome/plugins/pdk/go/host"
	"navidrome-lyrics-plugin/tags"
)

// LyricsResult represents lyrics fetched from a provider.
type LyricsResult struct {
	Text     string `json:"text"`
	Synced   bool   `json:"synced"`
	Provider string `json:"provider"`
}

// Provider defines the interface for external lyrics providers.
type Provider interface {
	Name() string
	FetchLyrics(title, artist, album string, duration int) (*LyricsResult, error)
}

// LRCLIBProvider fetches lyrics from the public LRCLIB API.
type LRCLIBProvider struct{}

type lrclibResponse struct {
	PlainLyrics  string `json:"plainLyrics"`
	SyncedLyrics string `json:"syncedLyrics"`
	Instrumental bool   `json:"instrumental"`
}

func (p *LRCLIBProvider) Name() string {
	return "lrclib"
}

var wordSyncRegex = regexp.MustCompile(`<[0-9]{2}:[0-9]{2}\.[0-9]{2,3}>`)

// CleanLyricsText removes ^translation annotations and <mm:ss.xxx> word-sync tags common in LRCLIB / TTML synced lyrics.
func CleanLyricsText(raw string) string {
	if raw == "" {
		return ""
	}
	if strings.Contains(raw, "<") {
		raw = wordSyncRegex.ReplaceAllString(raw, "")
	}
	if !strings.Contains(raw, "^") {
		return strings.TrimSpace(raw)
	}
	lines := strings.Split(raw, "\n")
	cleanedLines := make([]string, 0, len(lines))

	for _, line := range lines {
		if idx := strings.IndexByte(line, '^'); idx > 0 {
			line = strings.TrimSpace(line[:idx])
		}
		cleanedLines = append(cleanedLines, line)
	}

	return strings.Join(cleanedLines, "\n")
}


func (p *LRCLIBProvider) queryLRCLIB(title, artist, album string, duration int) (*LyricsResult, error) {
	apiURL := fmt.Sprintf("https://lrclib.net/api/get?track_name=%s&artist_name=%s",
		url.QueryEscape(title),
		url.QueryEscape(artist),
	)
	if strings.TrimSpace(album) != "" {
		apiURL += fmt.Sprintf("&album_name=%s", url.QueryEscape(album))
	}
	if duration > 0 {
		apiURL += fmt.Sprintf("&duration=%d", duration)
	}

	resp, err := host.HTTPSend(host.HTTPRequest{
		Method: "GET",
		URL:    apiURL,
	})
	if err != nil {
		return nil, fmt.Errorf("lrclib http error: %w", err)
	}
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("lrclib status code: %d", resp.StatusCode)
	}

	var res lrclibResponse
	if err := json.Unmarshal(resp.Body, &res); err != nil {
		return nil, fmt.Errorf("lrclib unmarshal error: %w", err)
	}

	if res.Instrumental {
		return &LyricsResult{
			Text:     "[Instrumental]",
			Synced:   false,
			Provider: p.Name(),
		}, nil
	}

	if res.SyncedLyrics != "" {
		return &LyricsResult{
			Text:     CleanLyricsText(res.SyncedLyrics),
			Synced:   true,
			Provider: p.Name(),
		}, nil
	}

	if res.PlainLyrics != "" {
		return &LyricsResult{
			Text:     CleanLyricsText(res.PlainLyrics),
			Synced:   false,
			Provider: p.Name(),
		}, nil
	}

	return nil, fmt.Errorf("no lyrics content returned")
}

func (p *LRCLIBProvider) queryLRCLIBSearch(title, artist string) (*LyricsResult, error) {
	apiURL := fmt.Sprintf("https://lrclib.net/api/search?q=%s",
		url.QueryEscape(title+" "+artist),
	)

	resp, err := host.HTTPSend(host.HTTPRequest{
		Method: "GET",
		URL:    apiURL,
	})
	if err != nil {
		return nil, fmt.Errorf("lrclib search http error: %w", err)
	}
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("lrclib search status code: %d", resp.StatusCode)
	}

	var results []lrclibResponse
	if err := json.Unmarshal(resp.Body, &results); err != nil {
		return nil, fmt.Errorf("lrclib search unmarshal error: %w", err)
	}

	for _, item := range results {
		if item.Instrumental {
			return &LyricsResult{
				Text:     "[Instrumental]",
				Synced:   false,
				Provider: p.Name(),
			}, nil
		}
		if item.SyncedLyrics != "" {
			return &LyricsResult{
				Text:     CleanLyricsText(item.SyncedLyrics),
				Synced:   true,
				Provider: p.Name(),
			}, nil
		}
		if item.PlainLyrics != "" {
			return &LyricsResult{
				Text:     CleanLyricsText(item.PlainLyrics),
				Synced:   false,
				Provider: p.Name(),
			}, nil
		}
	}

	return nil, fmt.Errorf("no lyrics found in search results")
}

func (p *LRCLIBProvider) FetchLyrics(title, artist, album string, duration int) (*LyricsResult, error) {
	titleCandidates := tags.ExtractTitleCandidates(title)
	artistList := tags.ExtractArtistCandidates(artist)

	type candidate struct {
		artist string
		album  string
	}

	var queryStages []candidate

	for _, a := range artistList {
		if strings.TrimSpace(album) != "" {
			queryStages = append(queryStages, candidate{artist: a, album: album})
		}
		queryStages = append(queryStages, candidate{artist: a, album: ""})
	}

	var lastErr error
	for _, t := range titleCandidates {
		for _, c := range queryStages {
			res, err := p.queryLRCLIB(t, c.artist, c.album, duration)
			if err == nil && res != nil && res.Text != "" {
				return res, nil
			}
			lastErr = err
		}

		// Search API fallback per title & artist candidate combination
		for _, a := range artistList {
			res, err := p.queryLRCLIBSearch(t, a)
			if err == nil && res != nil && res.Text != "" {
				return res, nil
			}
		}
	}

	if lastErr != nil {
		return nil, lastErr
	}
	return nil, fmt.Errorf("no lyrics found on lrclib")
}

// LyricsOVHProvider fetches plain lyrics from lyrics.ovh.
type LyricsOVHProvider struct{}

type ovhResponse struct {
	Lyrics string `json:"lyrics"`
}

func (p *LyricsOVHProvider) Name() string {
	return "ovh"
}

func (p *LyricsOVHProvider) queryOVH(title, artist string) (*LyricsResult, error) {
	apiURL := fmt.Sprintf("https://api.lyrics.ovh/v1/%s/%s",
		url.PathEscape(artist),
		url.PathEscape(title),
	)

	resp, err := host.HTTPSend(host.HTTPRequest{
		Method: "GET",
		URL:    apiURL,
	})
	if err != nil {
		return nil, fmt.Errorf("lyrics.ovh http error: %w", err)
	}
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("lyrics.ovh status code: %d", resp.StatusCode)
	}

	var res ovhResponse
	if err := json.Unmarshal(resp.Body, &res); err != nil {
		return nil, fmt.Errorf("lyrics.ovh unmarshal error: %w", err)
	}

	if strings.TrimSpace(res.Lyrics) == "" {
		return nil, fmt.Errorf("empty lyrics returned from lyrics.ovh")
	}

	return &LyricsResult{
		Text:     strings.TrimSpace(res.Lyrics),
		Synced:   false,
		Provider: p.Name(),
	}, nil
}

func (p *LyricsOVHProvider) FetchLyrics(title, artist, album string, duration int) (*LyricsResult, error) {
	// Try full artist first
	res, err := p.queryOVH(title, artist)
	if err == nil && res != nil && res.Text != "" {
		return res, nil
	}

	// Try primary artist if different
	primaryArtist := tags.ExtractPrimaryArtist(artist)
	if primaryArtist != "" && primaryArtist != artist {
		res, err := p.queryOVH(title, primaryArtist)
		if err == nil && res != nil && res.Text != "" {
			return res, nil
		}
	}

	return nil, fmt.Errorf("no lyrics found on lyrics.ovh")
}

// NetEaseProvider fetches synced .lrc lyrics from NetEase Cloud Music API.
type NetEaseProvider struct{}

func (p *NetEaseProvider) Name() string {
	return "netease"
}

type neteaseSearchResponse struct {
	Result struct {
		Songs []struct {
			ID int64 `json:"id"`
		} `json:"songs"`
	} `json:"result"`
}

type neteaseLyricResponse struct {
	Lrc struct {
		Lyric string `json:"lyric"`
	} `json:"lrc"`
}

func (p *NetEaseProvider) queryNetEase(title, artist string) (*LyricsResult, error) {
	searchURL := fmt.Sprintf("https://music.163.com/api/search/get/web?s=%s&type=1&offset=0&limit=1",
		url.QueryEscape(title+" "+artist),
	)

	resp, err := host.HTTPSend(host.HTTPRequest{
		Method: "GET",
		URL:    searchURL,
	})
	if err != nil || resp.StatusCode != 200 {
		return nil, fmt.Errorf("netease search error")
	}

	var searchRes neteaseSearchResponse
	if err := json.Unmarshal(resp.Body, &searchRes); err != nil || len(searchRes.Result.Songs) == 0 {
		return nil, fmt.Errorf("netease song not found")
	}

	songID := searchRes.Result.Songs[0].ID
	lyricURL := fmt.Sprintf("https://music.163.com/api/song/lyric?id=%d&lv=-1&kv=-1&tv=-1", songID)

	lyricResp, err := host.HTTPSend(host.HTTPRequest{
		Method: "GET",
		URL:    lyricURL,
	})
	if err != nil || lyricResp.StatusCode != 200 {
		return nil, fmt.Errorf("netease lyric fetch error")
	}

	var lyricRes neteaseLyricResponse
	if err := json.Unmarshal(lyricResp.Body, &lyricRes); err != nil || strings.TrimSpace(lyricRes.Lrc.Lyric) == "" {
		return nil, fmt.Errorf("netease empty lyric")
	}

	cleaned := CleanLyricsText(lyricRes.Lrc.Lyric)
	synced := strings.Contains(cleaned, "[")

	return &LyricsResult{
		Text:     cleaned,
		Synced:   synced,
		Provider: p.Name(),
	}, nil
}

func (p *NetEaseProvider) FetchLyrics(title, artist, album string, duration int) (*LyricsResult, error) {
	titleCandidates := tags.ExtractTitleCandidates(title)
	artistList := tags.ExtractArtistCandidates(artist)

	for _, t := range titleCandidates {
		for _, a := range artistList {
			res, err := p.queryNetEase(t, a)
			if err == nil && res != nil && res.Text != "" {
				return res, nil
			}
		}
	}
	return nil, fmt.Errorf("no lyrics found on netease")
}

