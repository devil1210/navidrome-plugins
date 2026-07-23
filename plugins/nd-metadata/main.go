package main

import (
	"encoding/json"
	"fmt"
	"net/url"
	"strings"

	"github.com/navidrome/navidrome/plugins/pdk/go/host"
	"github.com/navidrome/navidrome/plugins/pdk/go/lifecycle"
	"github.com/navidrome/navidrome/plugins/pdk/go/metadata"
	"github.com/navidrome/navidrome/plugins/pdk/go/pdk"
)

const (
	lastfmAPIKey = "c1e430c1de22b9e12971faacc5e1d565"
)

type ndMetadataPlugin struct{}

func init() {
	p := &ndMetadataPlugin{}
	lifecycle.Register(p)
	metadata.Register(p)
}

var (
	_ lifecycle.InitProvider           = (*ndMetadataPlugin)(nil)
	_ metadata.ArtistBiographyProvider = (*ndMetadataPlugin)(nil)
	_ metadata.AlbumInfoProvider       = (*ndMetadataPlugin)(nil)
)

func (p *ndMetadataPlugin) OnInit() error {
	pdk.Log(pdk.LogInfo, "ND Metadata Plugin v1.3.0 initialized")
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

type myMemoryResponse struct {
	ResponseData struct {
		TranslatedText string `json:"translatedText"`
	} `json:"responseData"`
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

func getRefreshIntervalDays() int64 {
	val, ok := host.ConfigGetInt("refresh_interval_days")
	if !ok || val < 0 {
		return 0
	}
	return val
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

	return strings.TrimSpace(t)
}

func isValidBio(text string) bool {
	cleaned := cleanBioText(text)
	return len(cleaned) >= 35
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
	if len(cleaned) < 20 {
		return "", fmt.Errorf("no valid album description")
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
	if len(cleaned) >= 20 {
		return cleaned, nil
	}

	return "", fmt.Errorf("no extract found")
}

func translateText(text string, sourceLang string, targetLang string) (string, error) {
	if len(text) == 0 {
		return text, nil
	}

	queryText := text
	if len(queryText) > 800 {
		queryText = queryText[:800]
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
		return text, err
	}
	if resp.StatusCode != 200 {
		return text, fmt.Errorf("mymemory returned status %d", resp.StatusCode)
	}

	var data myMemoryResponse
	if err := json.Unmarshal(resp.Body, &data); err != nil {
		return text, err
	}

	translated := data.ResponseData.TranslatedText
	if translated != "" {
		return cleanBioText(translated), nil
	}

	return text, nil
}

func (p *ndMetadataPlugin) GetArtistBiography(req metadata.ArtistRequest) (*metadata.ArtistBiographyResponse, error) {
	targetLangs, primaryLang := getTargetLanguages()
	refreshDays := getRefreshIntervalDays()
	cacheKey := "bio:" + strings.ToLower(req.Name)

	pdk.Log(pdk.LogInfo, fmt.Sprintf("Fetching biography for artist: %s (target languages: %v, primary: %s, refresh interval: %d days)", req.Name, targetLangs, primaryLang, refreshDays))

	// 0. If refreshDays > 0, try fetching from KVStore cache
	if refreshDays > 0 {
		cachedBioBytes, found, errKV := host.KVStoreGet(cacheKey)
		if errKV == nil && found && len(cachedBioBytes) > 30 {
			cachedBio := string(cachedBioBytes)
			pdk.Log(pdk.LogInfo, fmt.Sprintf("Returning cached biography for artist %s (%d days TTL)", req.Name, refreshDays))
			return &metadata.ArtistBiographyResponse{Biography: cachedBio}, nil
		}
	}

	var resultBio string

	// 1. Try fetching native biography from Last.fm in preferred target languages
	for _, lang := range targetLangs {
		bio, err := fetchLastFmBio(req.Name, lang)
		if err == nil && isValidBio(bio) {
			pdk.Log(pdk.LogInfo, fmt.Sprintf("Found valid native biography from Last.fm for %s (lang=%s)", req.Name, lang))
			resultBio = bio
			break
		}
	}

	// 2. Try fetching Wikipedia summary in primary target language
	if resultBio == "" {
		wikiNative, errWiki := fetchWikipediaSummary(req.Name, primaryLang)
		if errWiki == nil && isValidBio(wikiNative) {
			pdk.Log(pdk.LogInfo, fmt.Sprintf("Found native Wikipedia summary for %s (lang=%s)", req.Name, primaryLang))
			resultBio = wikiNative
		}
	}

	// 3. Fallback: If auto-translation enabled, fetch English or Japanese bio/wiki and translate to primary target language
	if resultBio == "" && isAutoTranslateEnabled() {
		// Try English Last.fm
		engBio, errEng := fetchLastFmBio(req.Name, "en")
		if errEng == nil && isValidBio(engBio) {
			pdk.Log(pdk.LogInfo, fmt.Sprintf("Translating English Last.fm bio for %s to %s...", req.Name, primaryLang))
			translated, errTrans := translateText(engBio, "en", primaryLang)
			if errTrans == nil && isValidBio(translated) {
				resultBio = translated
			} else {
				resultBio = engBio
			}
		}

		// Try English Wikipedia
		if resultBio == "" {
			wikiEn, errWikiEn := fetchWikipediaSummary(req.Name, "en")
			if errWikiEn == nil && isValidBio(wikiEn) {
				pdk.Log(pdk.LogInfo, fmt.Sprintf("Translating English Wikipedia summary for %s to %s...", req.Name, primaryLang))
				translated, errTrans := translateText(wikiEn, "en", primaryLang)
				if errTrans == nil && isValidBio(translated) {
					resultBio = translated
				} else {
					resultBio = wikiEn
				}
			}
		}

		// Try Japanese Wikipedia for J-Pop / J-Rock (e.g., Atarayo / あたらよ)
		if resultBio == "" && (strings.EqualFold(req.Name, "Atarayo") || strings.Contains(strings.ToLower(req.Name), "atarayo")) {
			jaText, errJa := fetchWikipediaSummary("あたらよ", "ja")
			if errJa == nil && len(jaText) > 10 {
				pdk.Log(pdk.LogInfo, fmt.Sprintf("Translating Japanese Wikipedia summary for %s to %s...", req.Name, primaryLang))
				translated, errTrans := translateText(jaText, "ja", primaryLang)
				if errTrans == nil && isValidBio(translated) {
					resultBio = translated
				}
			}
		}
	}

	if resultBio != "" {
		if refreshDays > 0 {
			ttlSeconds := refreshDays * 86400
			_ = host.KVStoreSetWithTTL(cacheKey, []byte(resultBio), ttlSeconds)
		}
		return &metadata.ArtistBiographyResponse{Biography: resultBio}, nil
	}

	return nil, fmt.Errorf("no biography found for artist %s", req.Name)
}

func (p *ndMetadataPlugin) GetAlbumInfo(req metadata.AlbumRequest) (*metadata.AlbumInfoResponse, error) {
	targetLangs, primaryLang := getTargetLanguages()
	pdk.Log(pdk.LogInfo, fmt.Sprintf("Fetching album info for %s by %s (primary lang: %s)", req.Name, req.Artist, primaryLang))

	for _, lang := range targetLangs {
		desc, err := fetchLastFmAlbumDesc(req.Artist, req.Name, lang)
		if err == nil && len(desc) > 10 {
			return &metadata.AlbumInfoResponse{
				Name:        req.Name,
				Description: desc,
			}, nil
		}
	}

	if isAutoTranslateEnabled() {
		descEng, errEng := fetchLastFmAlbumDesc(req.Artist, req.Name, "en")
		if errEng == nil && len(descEng) > 10 {
			translated, errTrans := translateText(descEng, "en", primaryLang)
			if errTrans == nil && len(translated) > 10 {
				return &metadata.AlbumInfoResponse{
					Name:        req.Name,
					Description: translated,
				}, nil
			}
		}
	}

	return nil, fmt.Errorf("no album info found for %s", req.Name)
}

func main() {}
