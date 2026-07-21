package main

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/navidrome/navidrome/plugins/pdk/go/lifecycle"
	"github.com/navidrome/navidrome/plugins/pdk/go/pdk"

	"navidrome-lyrics-plugin/providers"
	"navidrome-lyrics-plugin/tags"
)

type lyricsPlugin struct {
	tagExtractor *tags.TagExtractor
	providers    []providers.Provider
}

func init() {
	lifecycle.Register(&lyricsPlugin{
		tagExtractor: tags.NewTagExtractor(),
		providers: []providers.Provider{
			&providers.LRCLIBProvider{},
			&providers.LyricsOVHProvider{},
		},
	})
}

var _ lifecycle.InitProvider = (*lyricsPlugin)(nil)

func (p *lyricsPlugin) OnInit() error {
	pdk.Log(pdk.LogInfo, "Lyrics plugin initialized with native tag and online providers")
	return nil
}

type LyricsRequest struct {
	Title    string            `json:"title"`
	Artist   string            `json:"artist"`
	Album    string            `json:"album"`
	Duration int               `json:"duration"`
	Tags     map[string]string `json:"tags,omitempty"`
}

type LyricsResponse struct {
	Text     string `json:"text"`
	Synced   bool   `json:"synced"`
	Provider string `json:"provider"`
}

func (p *lyricsPlugin) FetchLyrics(reqPayload []byte) ([]byte, error) {
	var req LyricsRequest
	if err := json.Unmarshal(reqPayload, &req); err != nil {
		return nil, fmt.Errorf("invalid lyrics request: %w", err)
	}

	if len(req.Tags) > 0 {
		if tagLyrics, found := p.tagExtractor.ExtractFromMetadata(req.Tags); found {
			pdk.Log(pdk.LogInfo, fmt.Sprintf("Lyrics found in embedded audio tags for %s - %s", req.Artist, req.Title))
			return json.Marshal(LyricsResponse{
				Text:     tagLyrics,
				Synced:   strings.Contains(tagLyrics, "[00:"),
				Provider: "native_tag",
			})
		}
	}

	queryTitle := req.Title
	queryArtist := req.Artist
	queryAlbum := req.Album

	if len(req.Tags) > 0 {
		origTitle, origArtist, origAlbum := p.tagExtractor.ExtractOriginalMetadata(req.Tags)
		if origTitle != "" {
			pdk.Log(pdk.LogInfo, fmt.Sprintf("Using ORIGINALTITLE: %q (was %q)", origTitle, queryTitle))
			queryTitle = origTitle
		}
		if origArtist != "" {
			pdk.Log(pdk.LogInfo, fmt.Sprintf("Using ORIGINALARTIST: %q (was %q)", origArtist, queryArtist))
			queryArtist = origArtist
		}
		if origAlbum != "" {
			pdk.Log(pdk.LogInfo, fmt.Sprintf("Using ORIGINALALBUM: %q (was %q)", origAlbum, queryAlbum))
			queryAlbum = origAlbum
		}
	}

	for _, prov := range p.providers {
		pdk.Log(pdk.LogInfo, fmt.Sprintf("Querying provider %s for %s - %s (album=%q)", prov.Name(), queryArtist, queryTitle, queryAlbum))
		res, err := prov.FetchLyrics(queryTitle, queryArtist, queryAlbum, req.Duration)
		if err == nil && res != nil && res.Text != "" {
			return json.Marshal(LyricsResponse{
				Text:     res.Text,
				Synced:   res.Synced,
				Provider: res.Provider,
			})
		}
	}

	return nil, fmt.Errorf("no lyrics found for %s - %s", queryArtist, queryTitle)
}

func main() {}
