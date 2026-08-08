package providers

import (
	"encoding/json"
	"fmt"
	"math"
	"net/url"
	"strings"

	"github.com/navidrome/navidrome/plugins/pdk/go/host"
	"github.com/navidrome/navidrome/plugins/pdk/go/pdk"
	"navidrome-lyrics-plugin/tags"
)

var cachedMusixmatchToken string

func getMusixmatchToken() string {
	if cachedMusixmatchToken != "" {
		return cachedMusixmatchToken
	}

	tokURL := "https://apic-desktop.musixmatch.com/ws/1.1/token.get?app_id=web-desktop-app-v1.0"
	resp, err := host.HTTPSend(host.HTTPRequest{
		Method: "GET",
		URL:    tokURL,
		Headers: map[string]string{
			"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
		},
	})
	if err == nil && resp.StatusCode == 200 {
		var tokRes struct {
			Message struct {
				Body struct {
					UserToken string `json:"user_token"`
				} `json:"body"`
			} `json:"message"`
		}
		if err := json.Unmarshal(resp.Body, &tokRes); err == nil && tokRes.Message.Body.UserToken != "" {
			cachedMusixmatchToken = tokRes.Message.Body.UserToken
			pdk.Log(pdk.LogInfo, fmt.Sprintf("Acquired Musixmatch user_token: %s", cachedMusixmatchToken))
			return cachedMusixmatchToken
		}
	}

	// Fallback token
	cachedMusixmatchToken = "26080701bbea0d63c1c51213ff492cbbb4383163c9047aa8f6ebf1"
	return cachedMusixmatchToken
}

// MusixmatchRichSyncProvider fetches word-level karaoke synced lyrics from Musixmatch RichSync API.
type MusixmatchRichSyncProvider struct{}

func (p *MusixmatchRichSyncProvider) Name() string {
	return "musixmatch-richsync"
}

type musixmatchTrack struct {
	TrackID      int64  `json:"track_id"`
	TrackName    string `json:"track_name"`
	ArtistName   string `json:"artist_name"`
	HasRichSync  int    `json:"has_richsync"`
	HasSubtitles int    `json:"has_subtitles"`
}

type musixmatchSearchResponse struct {
	Message struct {
		Body struct {
			TrackList []struct {
				Track musixmatchTrack `json:"track"`
			} `json:"track_list"`
		} `json:"body"`
	} `json:"message"`
}

type richsyncWord struct {
	C string  `json:"c"`
	O float64 `json:"o"`
}

type richsyncLine struct {
	TS float64        `json:"ts"`
	TE float64        `json:"te"`
	L  []richsyncWord `json:"l"`
}

func parseRichSyncBody(richsyncBody string) (string, error) {
	if richsyncBody == "" {
		return "", fmt.Errorf("empty richsync body")
	}

	var lines []richsyncLine
	if err := json.Unmarshal([]byte(richsyncBody), &lines); err != nil {
		return "", fmt.Errorf("unmarshal richsync_body error: %w", err)
	}

	if len(lines) == 0 {
		return "", fmt.Errorf("no lines in richsync")
	}

	var lrcLines []string
	for _, l := range lines {
		ts := l.TS
		minS := int(ts) / 60
		secS := int(ts) % 60
		csS := int(math.Round((ts - math.Floor(ts)) * 100))
		if csS >= 100 {
			secS++
			csS -= 100
		}
		if secS >= 60 {
			minS++
			secS -= 60
		}

		lineStr := fmt.Sprintf("[%02d:%02d.%02d]", minS, secS, csS)
		for _, w := range l.L {
			c := w.C
			if c == "" {
				continue
			}
			wordTS := ts + w.O
			wMin := int(wordTS) / 60
			wSec := int(wordTS) % 60
			wCS := int(math.Round((wordTS - math.Floor(wordTS)) * 100))
			if wCS >= 100 {
				wSec++
				wCS -= 100
			}
			if wSec >= 60 {
				wMin++
				wSec -= 60
			}

			if strings.TrimSpace(c) == "" {
				lineStr += c
			} else {
				lineStr += fmt.Sprintf("<%02d:%02d.%02d>%s", wMin, wSec, wCS, c)
			}
		}
		lrcLines = append(lrcLines, lineStr)
	}

	result := strings.Join(lrcLines, "\n")
	if strings.TrimSpace(result) == "" {
		return "", fmt.Errorf("formatted richsync is empty")
	}

	return result, nil
}

var rejectKeywords = []string{"demo", "outtake", "commentary", "session", "work tape", "tribute", "karaoke", "cover", "instrumental"}

func isValidTrackVariant(cleanTitle, candidateTitle string) bool {
	cleanLower := strings.ToLower(cleanTitle)
	candLower := strings.ToLower(candidateTitle)

	for _, kw := range rejectKeywords {
		if strings.Contains(candLower, kw) && !strings.Contains(cleanLower, kw) {
			return false
		}
	}

	if strings.Contains(candLower, "live") && !strings.Contains(cleanLower, "live") {
		return false
	}

	return true
}

func searchMusixmatchTracks(tok, title, artist string) ([]musixmatchTrack, error) {
	qArt := url.QueryEscape(artist)
	qTrk := url.QueryEscape(title)
	qFull := url.QueryEscape(title + " " + artist)

	searchURLs := []string{
		fmt.Sprintf("https://apic-desktop.musixmatch.com/ws/1.1/track.search?format=json&q_artist=%s&q_track=%s&page_size=5&usertoken=%s&app_id=web-desktop-app-v1.0", qArt, qTrk, tok),
		fmt.Sprintf("https://apic-desktop.musixmatch.com/ws/1.1/track.search?format=json&q=%s&page_size=5&usertoken=%s&app_id=web-desktop-app-v1.0", qFull, tok),
		fmt.Sprintf("https://apic-desktop.musixmatch.com/ws/1.1/track.search?format=json&q_track=%s&page_size=5&usertoken=%s&app_id=web-desktop-app-v1.0", qTrk, tok),
	}

	headers := map[string]string{
		"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
	}

	var tracks []musixmatchTrack
	seen := make(map[int64]bool)

	for _, sURL := range searchURLs {
		resp, err := host.HTTPSend(host.HTTPRequest{
			Method:  "GET",
			URL:     sURL,
			Headers: headers,
		})
		if err != nil || resp.StatusCode != 200 {
			continue
		}

		var searchRes musixmatchSearchResponse
		if err := json.Unmarshal(resp.Body, &searchRes); err != nil {
			continue
		}

		hasRich := false
		for _, item := range searchRes.Message.Body.TrackList {
			trk := item.Track
			if trk.TrackID != 0 && !seen[trk.TrackID] {
				if !isValidTrackVariant(title, trk.TrackName) {
					continue
				}
				seen[trk.TrackID] = true
				tracks = append(tracks, trk)
				if trk.HasRichSync == 1 {
					hasRich = true
				}
			}
		}

		if hasRich {
			break
		}
	}

	if len(tracks) == 0 {
		return nil, fmt.Errorf("no musixmatch tracks found for %s - %s", artist, title)
	}

	return tracks, nil
}

func (p *MusixmatchRichSyncProvider) FetchLyrics(title, artist, album string, duration int) (*LyricsResult, error) {
	titleCandidates := tags.ExtractTitleCandidates(title)
	artistList := tags.ExtractArtistCandidates(artist)

	tok := getMusixmatchToken()
	headers := map[string]string{
		"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
	}

	for _, t := range titleCandidates {
		for _, a := range artistList {
			tracks, err := searchMusixmatchTracks(tok, t, a)
			if err != nil {
				continue
			}

			// Prioritize tracks with has_richsync == 1
			for _, trk := range tracks {
				if trk.HasRichSync == 1 {
					richURL := fmt.Sprintf("https://apic-desktop.musixmatch.com/ws/1.1/track.richsync.get?format=json&track_id=%d&usertoken=%s&app_id=web-desktop-app-v1.0", trk.TrackID, tok)
					resp, err := host.HTTPSend(host.HTTPRequest{
						Method:  "GET",
						URL:     richURL,
						Headers: headers,
					})
					if err != nil || resp.StatusCode != 200 {
						continue
					}

					var rRes struct {
						Message struct {
							Body struct {
								RichSync struct {
									RichSyncBody string `json:"richsync_body"`
								} `json:"richsync"`
							} `json:"body"`
						} `json:"message"`
					}

					if err := json.Unmarshal(resp.Body, &rRes); err == nil && rRes.Message.Body.RichSync.RichSyncBody != "" {
						lrcText, err := parseRichSyncBody(rRes.Message.Body.RichSync.RichSyncBody)
						if err == nil && lrcText != "" {
							return &LyricsResult{
								Text:     lrcText,
								Synced:   true,
								Provider: p.Name(),
							}, nil
						}
					}
				}
			}
		}
	}

	return nil, fmt.Errorf("no richsync lyrics found on musixmatch")
}

// MusixmatchSubtitleProvider fetches line-synced subtitles from Musixmatch.
type MusixmatchSubtitleProvider struct{}

func (p *MusixmatchSubtitleProvider) Name() string {
	return "musixmatch-subtitle"
}

func (p *MusixmatchSubtitleProvider) FetchLyrics(title, artist, album string, duration int) (*LyricsResult, error) {
	titleCandidates := tags.ExtractTitleCandidates(title)
	artistList := tags.ExtractArtistCandidates(artist)

	tok := getMusixmatchToken()
	headers := map[string]string{
		"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
	}

	for _, t := range titleCandidates {
		for _, a := range artistList {
			tracks, err := searchMusixmatchTracks(tok, t, a)
			if err != nil {
				continue
			}

			for _, trk := range tracks {
				subURL := fmt.Sprintf("https://apic-desktop.musixmatch.com/ws/1.1/track.subtitle.get?format=json&track_id=%d&usertoken=%s&app_id=web-desktop-app-v1.0", trk.TrackID, tok)
				resp, err := host.HTTPSend(host.HTTPRequest{
					Method:  "GET",
					URL:     subURL,
					Headers: headers,
				})
				if err != nil || resp.StatusCode != 200 {
					continue
				}

				var subRes struct {
					Message struct {
						Body struct {
							Subtitle struct {
								SubtitleBody string `json:"subtitle_body"`
							} `json:"subtitle"`
						} `json:"body"`
					} `json:"message"`
				}

				if err := json.Unmarshal(resp.Body, &subRes); err == nil {
					subBody := subRes.Message.Body.Subtitle.SubtitleBody
					if strings.TrimSpace(subBody) != "" {
						return &LyricsResult{
							Text:     CleanLyricsText(subBody),
							Synced:   true,
							Provider: p.Name(),
						}, nil
					}
				}
			}
		}
	}

	return nil, fmt.Errorf("no subtitle lyrics found on musixmatch")
}
