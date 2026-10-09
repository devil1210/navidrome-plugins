package main

import (
	"encoding/json"
	"fmt"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/navidrome/navidrome/plugins/pdk/go/host"
	"github.com/navidrome/navidrome/plugins/pdk/go/lifecycle"
	"github.com/navidrome/navidrome/plugins/pdk/go/metadata"
	"github.com/navidrome/navidrome/plugins/pdk/go/pdk"
	"github.com/navidrome/navidrome/plugins/pdk/go/scrobbler"
	"github.com/navidrome/navidrome/plugins/pdk/go/types"
)

const (
	lastfmAPIKey = "c1e430c1de22b9e12971faacc5e1d565"
)

type ndMetadataPlugin struct{}

func init() {
	p := &ndMetadataPlugin{}
	lifecycle.Register(p)
	metadata.Register(p)
	scrobbler.Register(p)
}

var (
	_ lifecycle.InitProvider           = (*ndMetadataPlugin)(nil)
	_ metadata.ArtistBiographyProvider = (*ndMetadataPlugin)(nil)
	_ metadata.ArtistImagesProvider    = (*ndMetadataPlugin)(nil)
	_ metadata.AlbumInfoProvider       = (*ndMetadataPlugin)(nil)
	_ scrobbler.Scrobbler              = (*ndMetadataPlugin)(nil)
)

func (p *ndMetadataPlugin) OnInit() error {
	pdk.Log(pdk.LogInfo, "ND Metadata Plugin v1.5.0 (Auto-Refresh on Playback, HD Images & Junk Filter) initialized")
	return nil
}

type lastFmArtistBioResponse struct {
	Artist struct {
		Bio struct {
			Content string `json:"content"`
			Summary string `json:"summary"`
		} `json:"bio"`
	} `json:"artist"`
}

type lastFmAlbumInfoResponse struct {
	Album struct {
		Wiki struct {
			Content string `json:"content"`
			Summary string `json:"summary"`
		} `json:"wiki"`
	} `json:"album"`
}

type wikipediaSummaryResponse struct {
	Title   string `json:"title"`
	Extract string `json:"extract"`
}

type geniusSearchResponse struct {
	Response struct {
		Sections []struct {
			Type string `json:"type"`
			Hits []struct {
				Result struct {
					ID int `json:"id"`
				} `json:"result"`
			} `json:"hits"`
		} `json:"sections"`
	} `json:"response"`
}

type geniusArtistResponse struct {
	Response struct {
		Artist struct {
			Description struct {
				Dom struct {
					Children []interface{} `json:"children"`
				} `json:"dom"`
			} `json:"description"`
		} `json:"artist"`
	} `json:"response"`
}

type myMemoryResponse struct {
	ResponseData struct {
		TranslatedText string `json:"translatedText"`
	} `json:"responseData"`
}

func getSourcePriority() []string {
	cfg, ok := host.ConfigGet("source_priority")
	if !ok || strings.TrimSpace(cfg) == "" {
		return []string{"wikipedia", "lastfm", "genius", "applemusic", "deezer", "discogs"}
	}
	parts := strings.Split(cfg, ",")
	var sources []string
	for _, p := range parts {
		s := strings.ToLower(strings.TrimSpace(p))
		if s != "" {
			sources = append(sources, s)
		}
	}
	if len(sources) == 0 {
		return []string{"wikipedia", "lastfm", "genius", "applemusic", "deezer", "discogs"}
	}
	return sources
}

func getTargetLanguages() ([]string, string) {
	cfg, ok := host.ConfigGet("target_language")
	if !ok || strings.TrimSpace(cfg) == "" {
		return []string{"es", "mx", "us"}, "es"
	}

	parts := strings.Split(cfg, ",")
	var langs []string
	primaryLang := ""

	for _, p := range parts {
		code := strings.ToLower(strings.TrimSpace(p))
		if code != "" {
			langs = append(langs, code)
			if primaryLang == "" {
				if code == "mx" || code == "ar" || code == "cl" || code == "co" || code == "pe" {
					primaryLang = "es"
				} else if code == "us" || code == "gb" || code == "au" || code == "ca" {
					primaryLang = "en"
				} else if code == "br" {
					primaryLang = "pt"
				} else {
					primaryLang = code
				}
			}
		}
	}

	if len(langs) == 0 {
		return []string{"es"}, "es"
	}
	if primaryLang == "" {
		primaryLang = langs[0]
	}

	return langs, primaryLang
}

func isAutoTranslateEnabled() bool {
	cfg, ok := host.ConfigGet("enable_auto_translate")
	if !ok {
		return true
	}
	lower := strings.ToLower(strings.TrimSpace(cfg))
	return lower == "true" || lower == "1" || lower == "yes"
}

func isResetArtistImagesEnabled() bool {
	cfg, ok := host.ConfigGet("reset_artist_images")
	if !ok {
		return true
	}
	lower := strings.ToLower(strings.TrimSpace(cfg))
	return lower == "true" || lower == "1" || lower == "yes"
}

func isPlaybackRefreshEnabled() bool {
	cfg, ok := host.ConfigGet("enable_playback_refresh")
	if !ok {
		return true
	}
	lower := strings.ToLower(strings.TrimSpace(cfg))
	return lower == "true" || lower == "1" || lower == "yes"
}

func getRefreshIntervalDays() int64 {
	cfg, ok := host.ConfigGet("refresh_interval_days")
	if !ok || strings.TrimSpace(cfg) == "" {
		return 7
	}
	val, err := strconv.ParseInt(strings.TrimSpace(cfg), 10, 64)
	if err != nil || val <= 0 {
		return 7
	}
	return val
}

func getSyncServiceURL() string {
	cfg, ok := host.ConfigGet("sync_service_url")
	if !ok || strings.TrimSpace(cfg) == "" {
		return "http://127.0.0.1:4535/sync_artist"
	}
	return strings.TrimSpace(cfg)
}

func cleanBioText(text string) string {
	t := text
	if idx := strings.Index(t, "<a href=\"https://www.last.fm/music/"); idx != -1 {
		t = t[:idx]
	}
	if idx := strings.Index(t, "Read more on Last.fm"); idx != -1 {
		t = t[:idx]
	}
	if idx := strings.Index(t, "User-contributed text is available under"); idx != -1 {
		t = t[:idx]
	}

	t = strings.ReplaceAll(t, "<p>", "")
	t = strings.ReplaceAll(t, "</p>", "")
	t = strings.ReplaceAll(t, "<i>", "")
	t = strings.ReplaceAll(t, "</i>", "")
	t = strings.ReplaceAll(t, "<b>", "")
	t = strings.ReplaceAll(t, "</b>", "")
	t = strings.ReplaceAll(t, "<br>", "\n")
	t = strings.ReplaceAll(t, "<br/>", "\n")

	return strings.TrimSpace(t)
}

var junkBioPhrases = []string{
	"etiquetas id3",
	"id3 tags",
	"mal asignadas",
	"incorrect tag",
	"correct tag",
	"is a misspelling of",
	"misspelled",
	"redirección",
	"redirect to",
	"etiquetas incorrectas",
	"nombre correcto",
}

func isValidBio(text string) bool {
	cleaned := cleanBioText(text)
	if len(cleaned) < 35 {
		return false
	}

	lower := strings.ToLower(cleaned)
	for _, junk := range junkBioPhrases {
		if strings.Contains(lower, junk) {
			return false
		}
	}

	return true
}

func fetchLastFmBio(artist string, lang string) (string, error) {
	endpoint := fmt.Sprintf("https://ws.audioscrobbler.com/2.0/?method=artist.getinfo&artist=%s&api_key=%s&lang=%s&format=json",
		url.QueryEscape(artist), lastfmAPIKey, lang)

	req := host.HTTPRequest{
		Method: "GET",
		URL:    endpoint,
		Headers: map[string]string{
			"User-Agent": "Navidrome/0.63.2 (https://www.navidrome.org)",
		},
	}

	resp, err := host.HTTPSend(req)
	if err != nil {
		return "", err
	}
	if resp.StatusCode != 200 {
		return "", fmt.Errorf("last.fm returned status %d", resp.StatusCode)
	}

	var data lastFmArtistBioResponse
	if err := json.Unmarshal(resp.Body, &data); err != nil {
		return "", err
	}

	bio := data.Artist.Bio.Content
	if !isValidBio(bio) {
		bio = data.Artist.Bio.Summary
	}
	cleaned := cleanBioText(bio)
	if !isValidBio(cleaned) {
		return "", fmt.Errorf("no valid biography in last.fm response")
	}

	return cleaned, nil
}

func fetchWikipediaSummary(title string, lang string) (string, error) {
	endpoint := fmt.Sprintf("https://%s.wikipedia.org/api/rest_v1/page/summary/%s",
		lang, url.QueryEscape(title))

	req := host.HTTPRequest{
		Method: "GET",
		URL:    endpoint,
		Headers: map[string]string{
			"User-Agent": "Navidrome/0.63.2 (https://www.navidrome.org)",
		},
	}

	resp, err := host.HTTPSend(req)
	if err != nil {
		return "", err
	}
	if resp.StatusCode != 200 {
		return "", fmt.Errorf("wikipedia %s returned status %d", lang, resp.StatusCode)
	}

	var data wikipediaSummaryResponse
	if err := json.Unmarshal(resp.Body, &data); err != nil {
		return "", err
	}

	cleaned := cleanBioText(data.Extract)
	if isValidBio(cleaned) {
		return cleaned, nil
	}

	return "", fmt.Errorf("no extract found")
}

func parseDomText(children []interface{}) string {
	var sb strings.Builder
	for _, child := range children {
		if str, ok := child.(string); ok {
			sb.WriteString(str)
		} else if m, ok := child.(map[string]interface{}); ok {
			if subChildren, ok := m["children"].([]interface{}); ok {
				sb.WriteString(parseDomText(subChildren))
			}
		}
	}
	return sb.String()
}

func fetchGeniusBio(artist string) (string, error) {
	searchURL := fmt.Sprintf("https://genius.com/api/search/multi?q=%s", url.QueryEscape(artist))
	req := host.HTTPRequest{
		Method: "GET",
		URL:    searchURL,
		Headers: map[string]string{
			"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
			"Accept":     "application/json",
		},
	}

	resp, err := host.HTTPSend(req)
	if err != nil {
		return "", err
	}
	if resp.StatusCode != 200 {
		return "", fmt.Errorf("genius search returned status %d", resp.StatusCode)
	}

	var searchData geniusSearchResponse
	if err := json.Unmarshal(resp.Body, &searchData); err != nil {
		return "", err
	}

	artistID := 0
	for _, sec := range searchData.Response.Sections {
		if sec.Type == "artist" && len(sec.Hits) > 0 {
			artistID = sec.Hits[0].Result.ID
			break
		}
	}

	if artistID == 0 {
		return "", fmt.Errorf("artist ID not found on Genius")
	}

	artURL := fmt.Sprintf("https://genius.com/api/artists/%d", artistID)
	reqArt := host.HTTPRequest{
		Method: "GET",
		URL:    artURL,
		Headers: map[string]string{
			"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
			"Accept":     "application/json",
		},
	}

	respArt, errArt := host.HTTPSend(reqArt)
	if errArt != nil {
		return "", errArt
	}
	if respArt.StatusCode != 200 {
		return "", fmt.Errorf("genius artist returned status %d", respArt.StatusCode)
	}

	var artData geniusArtistResponse
	if err := json.Unmarshal(respArt.Body, &artData); err != nil {
		return "", err
	}

	rawText := parseDomText(artData.Response.Artist.Description.Dom.Children)
	cleaned := cleanBioText(rawText)
	if isValidBio(cleaned) {
		return cleaned, nil
	}

	return "", fmt.Errorf("no valid Genius bio extract")
}

func isLikelyEnglish(text string) bool {
	lower := " " + strings.ToLower(text) + " "
	enMarkers := []string{" the ", " is ", " and ", " of ", " to ", " with ", " in ", " was ", " her ", " his ", " that ", " for ", " at ", " who ", " has ", " she ", " he "}
	esMarkers := []string{" el ", " la ", " los ", " las ", " de ", " en ", " y ", " que ", " con ", " por ", " para ", " es ", " su ", " una ", " un ", " como "}
	enCount := 0
	for _, w := range enMarkers {
		if strings.Contains(lower, w) {
			enCount++
		}
	}
	esCount := 0
	for _, w := range esMarkers {
		if strings.Contains(lower, w) {
			esCount++
		}
	}
	return enCount > esCount && enCount >= 3
}

func translateGoogle(text string, targetLang string) (string, error) {
	if len(strings.TrimSpace(text)) == 0 {
		return "", nil
	}
	endpoint := fmt.Sprintf("https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=%s&dt=t&q=%s",
		targetLang, url.QueryEscape(text))
	req := host.HTTPRequest{
		Method: "GET",
		URL:    endpoint,
		Headers: map[string]string{
			"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
		},
		TimeoutMs: 8000,
	}
	resp, err := host.HTTPSend(req)
	if err != nil {
		return "", err
	}
	if resp.StatusCode != 200 {
		return "", fmt.Errorf("google translate returned status %d", resp.StatusCode)
	}
	var raw []interface{}
	if err := json.Unmarshal(resp.Body, &raw); err != nil {
		return "", err
	}
	if len(raw) == 0 {
		return "", fmt.Errorf("empty google translate response")
	}
	parts, ok := raw[0].([]interface{})
	if !ok {
		return "", fmt.Errorf("invalid google translate parts format")
	}
	var sb strings.Builder
	for _, p := range parts {
		if pair, ok := p.([]interface{}); ok && len(pair) > 0 {
			if str, ok := pair[0].(string); ok {
				sb.WriteString(str)
			}
		}
	}
	res := strings.TrimSpace(sb.String())
	if res == "" {
		return "", fmt.Errorf("no text in google translate response")
	}
	return cleanBioText(res), nil
}

func translateSingleChunk(queryText string, sourceLang string, targetLang string) (string, error) {
	if len(queryText) == 0 {
		return "", nil
	}
	gTrans, errG := translateGoogle(queryText, targetLang)
	if errG == nil && gTrans != "" && !isLikelyEnglish(gTrans) {
		return gTrans, nil
	}

	if sourceLang == "" || sourceLang == "auto" {
		sourceLang = "AUTODETECT"
	}
	langpair := fmt.Sprintf("%s|%s", sourceLang, targetLang)
	endpoint := fmt.Sprintf("https://api.mymemory.translated.net/get?q=%s&langpair=%s",
		url.QueryEscape(queryText), langpair)

	req := host.HTTPRequest{
		Method: "GET",
		URL:    endpoint,
		Headers: map[string]string{
			"User-Agent": "Navidrome/0.63.2 (https://www.navidrome.org)",
		},
	}

	resp, err := host.HTTPSend(req)
	if err != nil {
		return queryText, err
	}
	if resp.StatusCode != 200 {
		return queryText, fmt.Errorf("mymemory returned status %d", resp.StatusCode)
	}

	var data myMemoryResponse
	if err := json.Unmarshal(resp.Body, &data); err != nil {
		return queryText, err
	}

	translated := data.ResponseData.TranslatedText
	if translated != "" {
		return cleanBioText(translated), nil
	}
	return queryText, nil
}

func translateText(text string, sourceLang string, targetLang string) (string, error) {
	if len(text) == 0 {
		return text, nil
	}
	// Try full text with Google Translate first (handles up to 4000 chars cleanly)
	if len(text) <= 4000 {
		gTrans, errG := translateGoogle(text, targetLang)
		if errG == nil && isValidBio(gTrans) && !isLikelyEnglish(gTrans) {
			return gTrans, nil
		}
	}

	if len(text) <= 450 {
		return translateSingleChunk(text, sourceLang, targetLang)
	}

	// Split by sentences or paragraphs so chunks are <= 450 characters
	var paragraphs []string
	if strings.Contains(text, "\n") {
		paragraphs = strings.Split(text, "\n")
	} else {
		rawSentences := strings.Split(text, ". ")
		var cur strings.Builder
		for _, s := range rawSentences {
			if cur.Len()+len(s)+2 > 400 && cur.Len() > 0 {
				paragraphs = append(paragraphs, strings.TrimSpace(cur.String()))
				cur.Reset()
			}
			if cur.Len() > 0 {
				cur.WriteString(". ")
			}
			cur.WriteString(s)
		}
		if cur.Len() > 0 {
			paragraphs = append(paragraphs, strings.TrimSpace(cur.String()))
		}
	}

	var translatedChunks []string
	for _, p := range paragraphs {
		p = strings.TrimSpace(p)
		if len(p) == 0 {
			continue
		}
		if len(p) > 450 {
			p = p[:450]
		}
		trans, err := translateSingleChunk(p, sourceLang, targetLang)
		if err == nil && trans != "" {
			translatedChunks = append(translatedChunks, trans)
		} else {
			translatedChunks = append(translatedChunks, p)
		}
	}

	if len(translatedChunks) > 0 {
		return strings.Join(translatedChunks, "\n\n"), nil
	}
	return text, nil
}

func (p *ndMetadataPlugin) GetArtistBiography(req metadata.ArtistRequest) (*metadata.ArtistBiographyResponse, error) {
	sources := getSourcePriority()
	targetLangs, primaryLang := getTargetLanguages()
	resetImages := isResetArtistImagesEnabled()

	pdk.Log(pdk.LogInfo, fmt.Sprintf("Fetching biography for artist: %s (resetImages=%v, source priority: %v, target languages: %v, primary: %s)", req.Name, resetImages, sources, targetLangs, primaryLang))

	var resultBio string

	// Iterate over prioritized sources in user-configured order
	for _, source := range sources {
		if resultBio != "" {
			break
		}

		switch source {
		case "wikipedia":
			wikiNative, errWiki := fetchWikipediaSummary(req.Name, primaryLang)
			if errWiki == nil && isValidBio(wikiNative) {
				pdk.Log(pdk.LogInfo, fmt.Sprintf("[Source: Wikipedia] Found native summary for %s (lang=%s)", req.Name, primaryLang))
				resultBio = wikiNative
			} else if isAutoTranslateEnabled() {
				wikiEn, errWikiEn := fetchWikipediaSummary(req.Name, "en")
				if errWikiEn == nil && isValidBio(wikiEn) {
					pdk.Log(pdk.LogInfo, fmt.Sprintf("[Source: Wikipedia] Translating English summary for %s to %s", req.Name, primaryLang))
					translated, errTrans := translateText(wikiEn, "en", primaryLang)
					if errTrans == nil && isValidBio(translated) {
						resultBio = translated
					}
				}
			}

		case "lastfm":
			for _, lang := range targetLangs {
				bio, err := fetchLastFmBio(req.Name, lang)
				if err == nil && isValidBio(bio) {
					if primaryLang == "es" && isLikelyEnglish(bio) {
						pdk.Log(pdk.LogInfo, fmt.Sprintf("[Source: Last.fm] Bio for %s is in English despite requesting %s, translating to %s", req.Name, lang, primaryLang))
						if isAutoTranslateEnabled() {
							translated, errTrans := translateText(bio, "en", primaryLang)
							if errTrans == nil && isValidBio(translated) {
								resultBio = translated
								break
							}
						}
					} else {
						pdk.Log(pdk.LogInfo, fmt.Sprintf("[Source: Last.fm] Found native bio for %s (lang=%s)", req.Name, lang))
						resultBio = bio
						break
					}
				}
			}
			if resultBio == "" && isAutoTranslateEnabled() {
				engBio, errEng := fetchLastFmBio(req.Name, "en")
				if errEng == nil && isValidBio(engBio) {
					pdk.Log(pdk.LogInfo, fmt.Sprintf("[Source: Last.fm] Translating English bio for %s to %s", req.Name, primaryLang))
					translated, errTrans := translateText(engBio, "en", primaryLang)
					if errTrans == nil && isValidBio(translated) {
						resultBio = translated
					}
				}
			}

		case "genius":
			geniusBio, errGenius := fetchGeniusBio(req.Name)
			if errGenius == nil && isValidBio(geniusBio) {
				pdk.Log(pdk.LogInfo, fmt.Sprintf("[Source: Genius] Found description for %s", req.Name))
				if isAutoTranslateEnabled() && (primaryLang == "es" && isLikelyEnglish(geniusBio)) {
					translated, errTrans := translateText(geniusBio, "en", primaryLang)
					if errTrans == nil && isValidBio(translated) {
						resultBio = translated
					} else {
						resultBio = geniusBio
					}
				} else {
					resultBio = geniusBio
				}
			}
		}
	}

	if resultBio != "" {
		if primaryLang == "es" && isLikelyEnglish(resultBio) && isAutoTranslateEnabled() {
			pdk.Log(pdk.LogInfo, fmt.Sprintf("Final biography for %s is still in English, translating to %s", req.Name, primaryLang))
			translated, errTrans := translateText(resultBio, "en", primaryLang)
			if errTrans == nil && isValidBio(translated) {
				resultBio = translated
			}
		}
		return &metadata.ArtistBiographyResponse{Biography: resultBio}, nil
	}

	return nil, fmt.Errorf("no biography found for artist %s across sources %v", req.Name, sources)
}

func (p *ndMetadataPlugin) GetAlbumInfo(req metadata.AlbumRequest) (*metadata.AlbumInfoResponse, error) {
	targetLangs, primaryLang := getTargetLanguages()

	for _, lang := range targetLangs {
		desc, err := fetchLastFmAlbumDesc(req.Artist, req.Name, lang)
		if err == nil && isValidBio(desc) {
			return &metadata.AlbumInfoResponse{
				Name:        req.Name,
				Description: desc,
			}, nil
		}
	}

	if isAutoTranslateEnabled() {
		descEng, errEng := fetchLastFmAlbumDesc(req.Artist, req.Name, "en")
		if errEng == nil && isValidBio(descEng) {
			translated, errTrans := translateText(descEng, "en", primaryLang)
			if errTrans == nil && isValidBio(translated) {
				return &metadata.AlbumInfoResponse{
					Name:        req.Name,
					Description: translated,
				}, nil
			}
		}
	}

	return nil, fmt.Errorf("no album info found for %s", req.Name)
}

func fetchLastFmAlbumDesc(artist, album string, lang string) (string, error) {
	endpoint := fmt.Sprintf("https://ws.audioscrobbler.com/2.0/?method=album.getinfo&artist=%s&album=%s&api_key=%s&lang=%s&format=json",
		url.QueryEscape(artist), url.QueryEscape(album), lastfmAPIKey, lang)

	req := host.HTTPRequest{
		Method: "GET",
		URL:    endpoint,
		Headers: map[string]string{
			"User-Agent": "Navidrome/0.63.2 (https://www.navidrome.org)",
		},
	}

	resp, err := host.HTTPSend(req)
	if err != nil {
		return "", err
	}
	if resp.StatusCode != 200 {
		return "", fmt.Errorf("last.fm returned status %d", resp.StatusCode)
	}

	var data lastFmAlbumInfoResponse
	if err := json.Unmarshal(resp.Body, &data); err != nil {
		return "", err
	}

	desc := data.Album.Wiki.Content
	if desc == "" {
		desc = data.Album.Wiki.Summary
	}
	cleaned := cleanBioText(desc)
	if !isValidBio(cleaned) {
		return "", fmt.Errorf("no valid album description")
	}

	return cleaned, nil
}

type deezerArtistSearchResponse struct {
	Data []struct {
		ID            int64  `json:"id"`
		Name          string `json:"name"`
		PictureSmall  string `json:"picture_small"`
		PictureMedium string `json:"picture_medium"`
		PictureBig    string `json:"picture_big"`
		PictureXL     string `json:"picture_xl"`
	} `json:"data"`
}

func fetchDeezerImages(artistName string) ([]metadata.ImageInfo, error) {
	cleanName := strings.TrimSpace(artistName)
	if cleanName == "" {
		return nil, fmt.Errorf("empty artist name")
	}

	endpoint := fmt.Sprintf("https://api.deezer.com/search/artist?q=%s&limit=3", url.QueryEscape(cleanName))
	req := host.HTTPRequest{
		Method: "GET",
		URL:    endpoint,
		Headers: map[string]string{
			"User-Agent": "Navidrome/1.0",
		},
		TimeoutMs: 6000,
	}

	resp, err := host.HTTPSend(req)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("deezer returned status %d", resp.StatusCode)
	}

	var data deezerArtistSearchResponse
	if err := json.Unmarshal(resp.Body, &data); err != nil {
		return nil, err
	}

	normTarget := strings.ToLower(strings.Map(func(r rune) rune {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') {
			return r
		}
		return -1
	}, cleanName))

	for _, item := range data.Data {
		candNorm := strings.ToLower(strings.Map(func(r rune) rune {
			if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') {
				return r
			}
			return -1
		}, item.Name))

		if normTarget == candNorm || strings.Contains(normTarget, candNorm) || strings.Contains(candNorm, normTarget) {
			var images []metadata.ImageInfo
			if item.PictureXL != "" {
				images = append(images, metadata.ImageInfo{URL: item.PictureXL, Size: 1000})
			}
			if item.PictureBig != "" {
				images = append(images, metadata.ImageInfo{URL: item.PictureBig, Size: 500})
			}
			if item.PictureMedium != "" {
				images = append(images, metadata.ImageInfo{URL: item.PictureMedium, Size: 250})
			}
			if item.PictureSmall != "" {
				images = append(images, metadata.ImageInfo{URL: item.PictureSmall, Size: 120})
			}
			if len(images) > 0 {
				return images, nil
			}
		}
	}

	return nil, fmt.Errorf("no matching Deezer image found for %s", cleanName)
}

func (p *ndMetadataPlugin) GetArtistImages(req metadata.ArtistRequest) (*metadata.ArtistImagesResponse, error) {
	pdk.Log(pdk.LogInfo, fmt.Sprintf("Fetching artist images for: %s", req.Name))
	imgs, err := fetchDeezerImages(req.Name)
	if err == nil && len(imgs) > 0 {
		return &metadata.ArtistImagesResponse{Images: imgs}, nil
	}
	return nil, fmt.Errorf("no images found for artist %s: %v", req.Name, err)
}

// Scrobbler Capability Implementation

func (p *ndMetadataPlugin) IsAuthorized(req scrobbler.IsAuthorizedRequest) (bool, error) {
	return true, nil
}

func (p *ndMetadataPlugin) NowPlaying(req scrobbler.NowPlayingRequest) error {
	return p.handlePlayback(req.Track.Artist, req.Track.Artists)
}

func (p *ndMetadataPlugin) Scrobble(req scrobbler.ScrobbleRequest) error {
	return p.handlePlayback(req.Track.Artist, req.Track.Artists)
}

func (p *ndMetadataPlugin) PlaybackReport(req scrobbler.PlaybackReportRequest) error {
	return nil
}

func (p *ndMetadataPlugin) handlePlayback(artistName string, artists []types.ArtistRef) error {
	if !isPlaybackRefreshEnabled() {
		return nil
	}
	intervalDays := getRefreshIntervalDays()
	if intervalDays <= 0 {
		intervalDays = 7
	}
	syncURL := getSyncServiceURL()

	var targetArtists []types.ArtistRef
	if len(artists) > 0 {
		targetArtists = artists
	} else if strings.TrimSpace(artistName) != "" {
		targetArtists = append(targetArtists, types.ArtistRef{Name: strings.TrimSpace(artistName)})
	}

	now := time.Now().Unix()

	for _, a := range targetArtists {
		name := strings.TrimSpace(a.Name)
		if name == "" {
			continue
		}
		kvKey := "last_refresh:" + strings.ToLower(name)
		valBytes, exists, errKV := host.KVStoreGet(kvKey)
		if errKV == nil && exists && len(valBytes) > 0 {
			lastTs, errParse := strconv.ParseInt(string(valBytes), 10, 64)
			if errParse == nil && (now-lastTs < intervalDays*86400) {
				daysAgo := float64(now-lastTs) / 86400.0
				pdk.Log(pdk.LogDebug, fmt.Sprintf("[Playback Refresh] Artist '%s' updated %.1f days ago (threshold: %d days). Skipping.", name, daysAgo, intervalDays))
				continue
			}
		}

		pdk.Log(pdk.LogInfo, fmt.Sprintf("[Playback Refresh] Checking/updating metadata for artist '%s' (threshold: %d days)", name, intervalDays))

		payload := map[string]interface{}{
			"artist_id":     a.ID,
			"artist_name":   name,
			"interval_days": intervalDays,
		}
		payloadBytes, errMarshal := json.Marshal(payload)
		if errMarshal != nil {
			continue
		}

		httpReq := host.HTTPRequest{
			Method: "POST",
			URL:    syncURL,
			Headers: map[string]string{
				"Content-Type": "application/json",
				"User-Agent":   "Navidrome-Metadata/1.5",
			},
			Body:      payloadBytes,
			TimeoutMs: 15000,
		}

		resp, errHTTP := host.HTTPSend(httpReq)
		if errHTTP != nil {
			pdk.Log(pdk.LogWarn, fmt.Sprintf("[Playback Refresh] Error calling sync service at %s for '%s': %v", syncURL, name, errHTTP))
			continue
		}

		if resp.StatusCode == 200 {
			_ = host.KVStoreSet(kvKey, []byte(strconv.FormatInt(now, 10)))
			pdk.Log(pdk.LogInfo, fmt.Sprintf("[Playback Refresh] Sync success for '%s': %s", name, string(resp.Body)))
		} else {
			pdk.Log(pdk.LogWarn, fmt.Sprintf("[Playback Refresh] Sync service returned status %d for '%s'", resp.StatusCode, name))
		}
	}

	return nil
}

func main() {}
