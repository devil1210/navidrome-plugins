package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/navidrome/navidrome/plugins/pdk/go/host"
	"github.com/navidrome/navidrome/plugins/pdk/go/lifecycle"
	"github.com/navidrome/navidrome/plugins/pdk/go/lyrics"
	"github.com/navidrome/navidrome/plugins/pdk/go/pdk"

	"navidrome-lyrics-plugin/providers"
	"navidrome-lyrics-plugin/tags"
)

type lyricsPlugin struct {
	tagExtractor *tags.TagExtractor
	providers    []providers.Provider
}

func init() {
	p := &lyricsPlugin{
		tagExtractor: tags.NewTagExtractor(),
		providers: []providers.Provider{
			&providers.LRCLIBProvider{},
			&providers.LyricsOVHProvider{},
		},
	}
	lifecycle.Register(p)
	lyrics.Register(p)
}

var (
	_ lifecycle.InitProvider = (*lyricsPlugin)(nil)
	_ lyrics.Lyrics         = (*lyricsPlugin)(nil)
)

func (p *lyricsPlugin) OnInit() error {
	return nil
}

func saveLRCFile(track lyrics.TrackInfo, lyricsText string) {
	if track.LibraryID <= 0 || track.Path == "" || strings.TrimSpace(lyricsText) == "" {
		return
	}
	lib, err := host.LibraryGetLibrary(track.LibraryID)
	if err != nil || lib == nil || lib.MountPoint == "" {
		pdk.Log(pdk.LogWarn, fmt.Sprintf("Could not get mount point for library %d: %v", track.LibraryID, err))
		return
	}

	ext := filepath.Ext(track.Path)
	if ext == "" {
		return
	}
	lrcRelPath := track.Path[:len(track.Path)-len(ext)] + ".lrc"
	lrcFullPath := filepath.Join(lib.MountPoint, lrcRelPath)

	if err := os.WriteFile(lrcFullPath, []byte(lyricsText), 0644); err != nil {
		pdk.Log(pdk.LogWarn, fmt.Sprintf("Failed to save .lrc file to %s: %v", lrcFullPath, err))
	} else {
		pdk.Log(pdk.LogInfo, fmt.Sprintf("Saved .lrc file successfully to %s", lrcFullPath))
	}
}

func (p *lyricsPlugin) GetLyrics(req lyrics.GetLyricsRequest) (lyrics.GetLyricsResponse, error) {
	track := req.Track
	queryTitle := track.Title
	queryArtist := track.Artist
	queryAlbum := track.Album
	durationSec := int(track.Duration)

	pdk.Log(pdk.LogInfo, fmt.Sprintf("GetLyrics called for artist=%q title=%q album=%q path=%q lib=%d", queryArtist, queryTitle, queryAlbum, track.Path, track.LibraryID))

	// 1. Try querying online providers (LRCLIB, lyrics.ovh)
	for _, prov := range p.providers {
		pdk.Log(pdk.LogInfo, fmt.Sprintf("Querying provider %s for %s - %s (album=%q)", prov.Name(), queryArtist, queryTitle, queryAlbum))
		res, err := prov.FetchLyrics(queryTitle, queryArtist, queryAlbum, durationSec)
		if err == nil && res != nil && res.Text != "" {
			pdk.Log(pdk.LogInfo, fmt.Sprintf("Lyrics found via provider %s for %s - %s", prov.Name(), queryArtist, queryTitle))

			// Save .lrc sidecar file to track folder on disk
			saveLRCFile(track, res.Text)

			return lyrics.GetLyricsResponse{
				Lyrics: []lyrics.LyricsText{
					{
						Text: res.Text,
					},
				},
			}, nil
		}
	}

	pdk.Log(pdk.LogWarn, fmt.Sprintf("No lyrics found for artist=%q title=%q", queryArtist, queryTitle))
	return lyrics.GetLyricsResponse{}, fmt.Errorf("no lyrics found for %s - %s", queryArtist, queryTitle)
}

func main() {}
