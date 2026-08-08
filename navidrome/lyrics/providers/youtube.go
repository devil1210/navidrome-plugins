package providers

import (
	"fmt"
	"math"
	"net/url"
	"regexp"
	"strconv"
	"strings"

	"github.com/navidrome/navidrome/plugins/pdk/go/host"
	"navidrome-lyrics-plugin/tags"
)

// YouTubeCaptionsProvider fetches official TimedText video captions from YouTube.
type YouTubeCaptionsProvider struct{}

func (p *YouTubeCaptionsProvider) Name() string {
	return "youtube-captions"
}

var (
	ytVideoIDRegex  = regexp.MustCompile(`"videoId":"([a-zA-Z0-9_-]{11})"`)
	ytTextNodeRegex = regexp.MustCompile(`<text start="([0-9.]+)"(?: dur="([0-9.]+)")?>([^<]*)</text>`)
)

func unescapeHTML(s string) string {
	s = strings.ReplaceAll(s, "&amp;", "&")
	s = strings.ReplaceAll(s, "&lt;", "<")
	s = strings.ReplaceAll(s, "&gt;", ">")
	s = strings.ReplaceAll(s, "&quot;", "\"")
	s = strings.ReplaceAll(s, "&#39;", "'")
	s = strings.ReplaceAll(s, "&apos;", "'")
	s = strings.ReplaceAll(s, "\n", " ")
	return strings.TrimSpace(s)
}

func (p *YouTubeCaptionsProvider) FetchLyrics(title, artist, album string, duration int) (*LyricsResult, error) {
	titleCandidates := tags.ExtractTitleCandidates(title)
	artistList := tags.ExtractArtistCandidates(artist)

	headers := map[string]string{
		"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
	}

	for _, t := range titleCandidates {
		for _, a := range artistList {
			query := url.QueryEscape(fmt.Sprintf("%s %s official audio lyrics", t, a))
			searchURL := fmt.Sprintf("https://www.youtube.com/results?search_query=%s", query)

			resp, err := host.HTTPSend(host.HTTPRequest{
				Method:  "GET",
				URL:     searchURL,
				Headers: headers,
			})
			if err != nil || resp.StatusCode != 200 {
				continue
			}

			matches := ytVideoIDRegex.FindStringSubmatch(string(resp.Body))
			if len(matches) < 2 {
				continue
			}

			videoID := matches[1]
			captionURL := fmt.Sprintf("https://www.youtube.com/api/timedtext?v=%s&lang=en", videoID)

			capResp, err := host.HTTPSend(host.HTTPRequest{
				Method:  "GET",
				URL:     captionURL,
				Headers: headers,
			})
			if err != nil || capResp.StatusCode != 200 || len(capResp.Body) == 0 {
				// Try fallback without lang=en
				captionURL = fmt.Sprintf("https://www.youtube.com/api/timedtext?v=%s", videoID)
				capResp, err = host.HTTPSend(host.HTTPRequest{
					Method:  "GET",
					URL:     captionURL,
					Headers: headers,
				})
				if err != nil || capResp.StatusCode != 200 || len(capResp.Body) == 0 {
					continue
				}
			}

			bodyStr := string(capResp.Body)
			if !strings.Contains(bodyStr, "<text") {
				continue
			}

			nodes := ytTextNodeRegex.FindAllStringSubmatch(bodyStr, -1)
			if len(nodes) == 0 {
				continue
			}

			var lrcLines []string
			for _, match := range nodes {
				if len(match) < 4 {
					continue
				}
				startSec, err := strconv.ParseFloat(match[1], 64)
				if err != nil {
					continue
				}
				text := unescapeHTML(match[3])
				if text == "" {
					continue
				}

				minS := int(startSec) / 60
				secS := int(startSec) % 60
				csS := int(math.Round((startSec - math.Floor(startSec)) * 100))
				if csS >= 100 {
					secS++
					csS -= 100
				}
				if secS >= 60 {
					minS++
					secS -= 60
				}

				lrcLines = append(lrcLines, fmt.Sprintf("[%02d:%02d.%02d]%s", minS, secS, csS, text))
			}

			if len(lrcLines) > 0 {
				lrcText := strings.Join(lrcLines, "\n")
				return &LyricsResult{
					Text:     lrcText,
					Synced:   true,
					Provider: p.Name(),
				}, nil
			}
		}
	}

	return nil, fmt.Errorf("no youtube captions found")
}
