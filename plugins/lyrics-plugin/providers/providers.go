package providers

import (
	"encoding/json"
	"fmt"
	"net/url"
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
			Text:     res.SyncedLyrics,
			Synced:   true,
			Provider: p.Name(),
		}, nil
	}

	if res.PlainLyrics != "" {
		return &LyricsResult{
			Text:     res.PlainLyrics,
			Synced:   false,
			Provider: p.Name(),
		}, nil
	}

	return nil, fmt.Errorf("no lyrics content returned")
}

func (p *LRCLIBProvider) FetchLyrics(title, artist, album string, duration int) (*LyricsResult, error) {
	primaryArtist := tags.ExtractPrimaryArtist(artist)
	hasPrimaryArtistFallback := primaryArtist != "" && primaryArtist != artist

	// Candidates for (artist, album) query stages
	type candidate struct {
		artist string
		album  string
	}

	var candidates []candidate

	// Stage 1: Full artist, full album
	if strings.TrimSpace(album) != "" {
		candidates = append(candidates, candidate{artist: artist, album: album})
	}
	// Stage 2: Full artist, no album (fallback for strict album_name 404s)
	candidates = append(candidates, candidate{artist: artist, album: ""})

	// Stage 3 & 4: Primary artist fallback (for multi-artist/collaboration strings)
	if hasPrimaryArtistFallback {
		if strings.TrimSpace(album) != "" {
			candidates = append(candidates, candidate{artist: primaryArtist, album: album})
		}
		candidates = append(candidates, candidate{artist: primaryArtist, album: ""})
	}

	var lastErr error
	for _, c := range candidates {
		res, err := p.queryLRCLIB(title, c.artist, c.album, duration)
		if err == nil && res != nil && res.Text != "" {
			return res, nil
		}
		lastErr = err
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
