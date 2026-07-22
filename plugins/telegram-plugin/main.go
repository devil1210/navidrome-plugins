package main

import (
	"bytes"
	"crypto/md5"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"mime/multipart"
	"net/url"
	"sort"
	"strconv"
	"strings"

	"github.com/navidrome/navidrome/plugins/pdk/go/host"
	"github.com/navidrome/navidrome/plugins/pdk/go/lifecycle"
	"github.com/navidrome/navidrome/plugins/pdk/go/pdk"
	"github.com/navidrome/navidrome/plugins/pdk/go/scheduler"
)

const (
	pollScheduleID  = "telegram-album-poll"
	kvLastSeenAlbum = "last_seen_album_id"
	defaultCronExpr = "*/1 * * * *"
	defaultPollSize = "20"

	cfgSubsonicUser        = "subsonic_user"
	cfgSubsonicPassword    = "subsonic_password"
	cfgSubsonicBaseURL     = "subsonic_base_url"
	cfgSubsonicClient      = "subsonic_client"
	cfgSubsonicAPIVersion  = "subsonic_api_version"
	cfgSubsonicCoverSize   = "subsonic_cover_size"
	cfgPollInterval        = "poll_interval"
	cfgMessageTitle        = "message_title"
	cfgMessageBody         = "message_body"
	cfgImageURLTemplate    = "image_url_template"
	cfgUseSubsonicCoverArt = "use_subsonic_cover_art"
	cfgTelegramBotToken    = "telegram_bot_token"
	cfgTelegramChatID      = "telegram_chat_id"
	cfgTelegramThreadID    = "telegram_thread_id"
	cfgTelegramParseMode   = "telegram_parse_mode"
)

const (
	defaultTitleTemplate  = "<h3>📚 {album}</h3>\n<h4>👤 {artist}</h4>"
	defaultBodyTemplate   = "<table bordered striped>\n  <tr>\n    <td><b>📅 Año</b></td>\n    <td>{year}</td>\n  </tr>\n  <tr>\n    <td><b>📦 Género</b></td>\n    <td>{genre}</td>\n  </tr>\n  <tr>\n    <td><b>💿 Discos</b></td>\n    <td>{discCount}</td>\n  </tr>\n  <tr>\n    <td><b>🔢 Canciones</b></td>\n    <td>{songCount}</td>\n  </tr>\n  <tr>\n    <td><b>🕒 Duración</b></td>\n    <td>{duration}</td>\n  </tr>\n</table>\n\n<details>\n  <summary>🎵 Ver Lista de Canciones</summary>\n  <blockquote>\n    {songs}\n  </blockquote>\n</details>\n\n<hr/>\n<a href=\"{url}\">🌐 Escuchar en Navidrome</a>"
	defaultSubsonicClient = "navidrome-telegram-plugin"
	defaultSubsonicAPI    = "1.16.1"
	defaultCoverSize      = "600"
)

type subsonicSong struct {
	ID         string `json:"id"`
	Title      string `json:"title"`
	Track      int    `json:"track"`
	Duration   int    `json:"duration"`
	DiscNumber int    `json:"discNumber"`
}

type subsonicAlbumResponse struct {
	SubsonicResponse struct {
		Status string `json:"status"`
		Album  struct {
			Song []subsonicSong `json:"song"`
		} `json:"album"`
	} `json:"subsonic-response"`
}

type subsonicAlbum struct {
	ID        string `json:"id"`
	Name      string `json:"name"`
	Artist    string `json:"artist"`
	ArtistID  string `json:"artistId"`
	Year      int    `json:"year"`
	Genre     string `json:"genre"`
	SongCount int    `json:"songCount"`
	Duration  int    `json:"duration"`
}

type subsonicAlbumList2Response struct {
	SubsonicResponse struct {
		Status     string `json:"status"`
		AlbumList2 struct {
			Album []subsonicAlbum `json:"album"`
		} `json:"albumList2"`
	} `json:"subsonic-response"`
}

type richMessageMediaDetails struct {
	Type  string `json:"type"`
	Media string `json:"media"`
}

type richMessageMedia struct {
	ID    string                  `json:"id"`
	Media richMessageMediaDetails `json:"media"`
}

type richMessage struct {
	HTML  string             `json:"html"`
	Media []richMessageMedia `json:"media,omitempty"`
}

type telegramPlugin struct{}

func init() {
	p := &telegramPlugin{}
	lifecycle.Register(p)
	scheduler.Register(p)
}

var (
	_ lifecycle.InitProvider     = (*telegramPlugin)(nil)
	_ scheduler.CallbackProvider = (*telegramPlugin)(nil)
)

func (p *telegramPlugin) OnInit() error {
	pdk.Log(pdk.LogInfo, "telegram album notifier initializing")

	cronExpr := defaultCronExpr
	if v, ok := pdk.GetConfig(cfgPollInterval); ok && strings.TrimSpace(v) != "" {
		cronExpr = strings.TrimSpace(v)
	}

	res, err := host.SchedulerScheduleRecurring(cronExpr, "poll", pollScheduleID)
	if err != nil {
		pdk.Log(pdk.LogError, fmt.Sprintf("failed to schedule album poll: %v", err))
		return fmt.Errorf("failed to schedule album poll: %w", err)
	}

	pdk.Log(pdk.LogInfo, fmt.Sprintf("album poll scheduled successfully ID=%s with cron: %s", res, cronExpr))
	return nil
}

func (p *telegramPlugin) OnCallback(input scheduler.SchedulerCallbackRequest) error {
	pdk.Log(pdk.LogDebug, fmt.Sprintf("scheduler callback fired: scheduleId=%s", input.ScheduleID))
	if input.ScheduleID != pollScheduleID {
		return nil
	}
	return pollAndNotify()
}

func pollAndNotify() error {
	botToken, _ := pdk.GetConfig(cfgTelegramBotToken)
	botToken = strings.TrimSpace(botToken)
	chatIDConfig, _ := pdk.GetConfig(cfgTelegramChatID)
	chatIDConfig = strings.TrimSpace(chatIDConfig)

	if botToken == "" || chatIDConfig == "" {
		pdk.Log(pdk.LogError, "telegram_bot_token and/or telegram_chat_id are not configured; skipping poll")
		return nil
	}

	subsonicUser, ok := pdk.GetConfig(cfgSubsonicUser)
	if !ok || strings.TrimSpace(subsonicUser) == "" {
		pdk.Log(pdk.LogError, "subsonic_user is not configured; skipping poll")
		return nil
	}

	albums, err := fetchNewestAlbums(subsonicUser)
	if err != nil {
		pdk.Log(pdk.LogError, fmt.Sprintf("failed to fetch album list: %v", err))
		return nil
	}

	if len(albums) == 0 {
		pdk.Log(pdk.LogInfo, "no albums returned by getAlbumList2")
		return nil
	}

	lastSeenID := loadLastSeenID()
	if lastSeenID == "" {
		if err := saveLastSeenID(albums[0].ID); err != nil {
			pdk.Log(pdk.LogWarn, fmt.Sprintf("failed to persist initial watermark: %v", err))
		} else {
			pdk.Log(pdk.LogInfo, fmt.Sprintf("first run: watermark set to album id=%s name=%q artist=%q", albums[0].ID, albums[0].Name, albums[0].Artist))
		}
		return nil
	}

	newAlbums := collectNewAlbums(albums, lastSeenID)
	if len(newAlbums) == 0 {
		pdk.Log(pdk.LogInfo, fmt.Sprintf("no new albums found; last_seen_album_id=%s newest_album_id=%s", lastSeenID, albums[0].ID))
		return nil
	}

	pdk.Log(pdk.LogInfo, fmt.Sprintf("found %d new album(s) since last_seen_album_id=%s", len(newAlbums), lastSeenID))

	// Limit processing to max 5 albums per poll to avoid WASM context deadline timeouts (typically 30 seconds)
	// and prevent Telegram API rate limits.
	maxBatch := 5
	isBatched := false
	if len(newAlbums) > maxBatch {
		pdk.Log(pdk.LogInfo, fmt.Sprintf("batching notification: only processing the %d oldest new albums in this poll", maxBatch))
		newAlbums = newAlbums[len(newAlbums)-maxBatch:]
		isBatched = true
	}

	for i := len(newAlbums) - 1; i >= 0; i-- {
		a := newAlbums[i]

		// Fetch tracklist for this album
		songs, err := fetchAlbumSongs(a.ID, subsonicUser)
		if err != nil {
			pdk.Log(pdk.LogWarn, fmt.Sprintf("failed to fetch songs for album %s (id=%s): %v", a.Name, a.ID, err))
		}

		title, body, imageURL := renderMessage(a, songs)
		pdk.Log(pdk.LogInfo, fmt.Sprintf("sending notification for album id=%s name=%q artist=%q image=%q", a.ID, a.Name, a.Artist, imageURL))

		if err := sendTelegramDirect(botToken, chatIDConfig, title, body, imageURL, a, songs); err != nil {
			pdk.Log(pdk.LogError, fmt.Sprintf("telegram POST failed for album %q: %v", a.ID, err))
			return nil
		}

		// Save the watermark progressively inside the loop after each successfully processed album.
		// This ensures that even if a subsequent album times out, we don't repeat the ones we've already notified.
		if err := saveLastSeenID(a.ID); err != nil {
			pdk.Log(pdk.LogWarn, fmt.Sprintf("failed to persist watermark for album %s: %v", a.ID, err))
		} else {
			pdk.Log(pdk.LogInfo, fmt.Sprintf("progressively updated last_seen_album_id=%s", a.ID))
		}
	}

	// If we processed the full list (or the final batch), we make sure the watermark matches the overall newest album.
	// This is a safety check to align the watermark.
	if !isBatched {
		if err := saveLastSeenID(albums[0].ID); err != nil {
			pdk.Log(pdk.LogWarn, fmt.Sprintf("failed to persist final last_seen_album_id: %v", err))
		} else {
			pdk.Log(pdk.LogInfo, fmt.Sprintf("final watermark aligned: updated last_seen_album_id=%s", albums[0].ID))
		}
	}

	return nil
}

func fetchNewestAlbums(username string) ([]subsonicAlbum, error) {
	uri := fmt.Sprintf("getAlbumList2?type=newest&size=%s&u=%s", defaultPollSize, username)
	responseJSON, err := host.SubsonicAPICall(uri)
	if err != nil {
		return nil, fmt.Errorf("subsonicapi call: %w", err)
	}

	var parsed subsonicAlbumList2Response
	if err := json.Unmarshal([]byte(responseJSON), &parsed); err != nil {
		return nil, fmt.Errorf("parse response: %w", err)
	}

	if parsed.SubsonicResponse.Status != "ok" {
		return nil, fmt.Errorf("subsonic response status: %s", parsed.SubsonicResponse.Status)
	}

	return parsed.SubsonicResponse.AlbumList2.Album, nil
}

func fetchAlbumSongs(albumID, username string) ([]subsonicSong, error) {
	uri := fmt.Sprintf("getAlbum?id=%s&u=%s", albumID, username)
	responseJSON, err := host.SubsonicAPICall(uri)
	if err != nil {
		return nil, fmt.Errorf("subsonicapi call: %w", err)
	}

	var parsed subsonicAlbumResponse
	if err := json.Unmarshal([]byte(responseJSON), &parsed); err != nil {
		return nil, fmt.Errorf("parse response: %w", err)
	}

	if parsed.SubsonicResponse.Status != "ok" {
		return nil, fmt.Errorf("subsonic response status: %s", parsed.SubsonicResponse.Status)
	}

	return parsed.SubsonicResponse.Album.Song, nil
}

func collectNewAlbums(albums []subsonicAlbum, lastSeenID string) []subsonicAlbum {
	var newOnes []subsonicAlbum
	for _, a := range albums {
		if a.ID == lastSeenID {
			break
		}
		newOnes = append(newOnes, a)
	}
	return newOnes
}

type discSongs struct {
	DiscNumber int
	Songs      []subsonicSong
}

func hasMultipleDiscs(songs []subsonicSong) bool {
	if len(songs) <= 1 {
		return false
	}
	firstDisc := songs[0].DiscNumber
	for _, s := range songs {
		if s.DiscNumber > 0 && s.DiscNumber != firstDisc {
			return true
		}
	}
	return false
}

func groupSongsByDisc(songs []subsonicSong) []discSongs {
	var groups []discSongs
	groupMap := make(map[int]int)

	for _, s := range songs {
		discNum := s.DiscNumber
		if discNum <= 0 {
			discNum = 1
		}
		if idx, exists := groupMap[discNum]; exists {
			groups[idx].Songs = append(groups[idx].Songs, s)
		} else {
			groupMap[discNum] = len(groups)
			groups = append(groups, discSongs{
				DiscNumber: discNum,
				Songs:      []subsonicSong{s},
			})
		}
	}
	return groups
}

func getDiscCount(songs []subsonicSong) int {
	if len(songs) == 0 {
		return 1
	}
	discMap := make(map[int]bool)
	for _, s := range songs {
		discNum := s.DiscNumber
		if discNum <= 0 {
			discNum = 1
		}
		discMap[discNum] = true
	}
	return len(discMap)
}

func formatSongList(songs []subsonicSong) string {
	if !hasMultipleDiscs(songs) {
		var sb strings.Builder
		for _, s := range songs {
			trackPrefix := ""
			if s.Track > 0 {
				trackPrefix = fmt.Sprintf("%d. ", s.Track)
			}
			durationSuffix := ""
			if s.Duration > 0 {
				durationSuffix = fmt.Sprintf(" (%s)", formatDuration(s.Duration))
			}
			sb.WriteString(fmt.Sprintf("%s%s%s\n", trackPrefix, s.Title, durationSuffix))
		}
		return strings.TrimSuffix(sb.String(), "\n")
	}

	groups := groupSongsByDisc(songs)
	sort.Slice(groups, func(i, j int) bool {
		return groups[i].DiscNumber < groups[j].DiscNumber
	})

	var sb strings.Builder
	for i, g := range groups {
		sb.WriteString(fmt.Sprintf("Disco %d:\n", g.DiscNumber))
		for _, s := range g.Songs {
			trackPrefix := ""
			if s.Track > 0 {
				trackPrefix = fmt.Sprintf("%d. ", s.Track)
			}
			durationSuffix := ""
			if s.Duration > 0 {
				durationSuffix = fmt.Sprintf(" (%s)", formatDuration(s.Duration))
			}
			sb.WriteString(fmt.Sprintf("%s%s%s\n", trackPrefix, s.Title, durationSuffix))
		}
		if i < len(groups)-1 {
			sb.WriteString("\n")
		}
	}
	return strings.TrimSuffix(sb.String(), "\n")
}

func formatSongListTelegram(songs []subsonicSong, parseMode string) string {
	isHTML := strings.ToLower(parseMode) == "html"
	isM2 := strings.ToLower(parseMode) == "markdownv2"
	isM1 := strings.ToLower(parseMode) == "markdown"

	if !hasMultipleDiscs(songs) {
		var sb strings.Builder
		if isHTML {
			sb.WriteString("<ul>\n")
		}
		for _, s := range songs {
			trackPrefix := ""
			if s.Track > 0 {
				if isM2 {
					trackPrefix = fmt.Sprintf("%d\\. ", s.Track)
				} else if isHTML {
					trackPrefix = fmt.Sprintf("<b>%02d.</b> ", s.Track)
				} else {
					trackPrefix = fmt.Sprintf("%d. ", s.Track)
				}
			}
			durationSuffix := ""
			if s.Duration > 0 {
				durationSuffix = fmt.Sprintf(" (%s)", formatDuration(s.Duration))
				if isM2 {
					durationSuffix = strings.NewReplacer("(", "\\(", ")", "\\)", ":", "\\:").Replace(durationSuffix)
				}
			}
			escapedTitle := escapeValue(s.Title, parseMode)
			if isHTML {
				sb.WriteString(fmt.Sprintf("  <li>%s%s%s</li>\n", trackPrefix, escapedTitle, durationSuffix))
			} else {
				sb.WriteString(fmt.Sprintf("%s%s%s\n", trackPrefix, escapedTitle, durationSuffix))
			}
		}
		if isHTML {
			sb.WriteString("</ul>")
		}
		return strings.TrimSuffix(sb.String(), "\n")
	}

	groups := groupSongsByDisc(songs)
	sort.Slice(groups, func(i, j int) bool {
		return groups[i].DiscNumber < groups[j].DiscNumber
	})

	var sb strings.Builder
	for i, g := range groups {
		discName := fmt.Sprintf("Disco %d", g.DiscNumber)
		if isHTML {
			sb.WriteString(fmt.Sprintf("<details open>\n  <summary>💿 %s</summary>\n  <ul>\n", escapeHTML(discName)))
			for _, s := range g.Songs {
				trackPrefix := ""
				if s.Track > 0 {
					trackPrefix = fmt.Sprintf("<b>%02d.</b> ", s.Track)
				}
				durationSuffix := ""
				if s.Duration > 0 {
					durationSuffix = fmt.Sprintf(" (%s)", formatDuration(s.Duration))
				}
				escapedTitle := escapeHTML(s.Title)
				sb.WriteString(fmt.Sprintf("    <li>%s%s%s</li>\n", trackPrefix, escapedTitle, durationSuffix))
			}
			sb.WriteString("  </ul>\n</details>")
			if i < len(groups)-1 {
				sb.WriteString("\n")
			}
		} else {
			var discHeader string
			if isM2 {
				escapedDiscName := escapeMarkdownV2(fmt.Sprintf("Disco %d", g.DiscNumber))
				discHeader = "*" + escapedDiscName + "*"
			} else if isM1 {
				discHeader = fmt.Sprintf("**Disco %d**", g.DiscNumber)
			} else {
				discHeader = fmt.Sprintf("Disco %d:", g.DiscNumber)
			}

			sb.WriteString(discHeader)
			sb.WriteString("\n")
			for _, s := range g.Songs {
				trackPrefix := ""
				if s.Track > 0 {
					if isM2 {
						trackPrefix = fmt.Sprintf("%d\\. ", s.Track)
					} else {
						trackPrefix = fmt.Sprintf("%d. ", s.Track)
					}
				}
				durationSuffix := ""
				if s.Duration > 0 {
					durationSuffix = fmt.Sprintf(" (%s)", formatDuration(s.Duration))
					if isM2 {
						durationSuffix = strings.NewReplacer("(", "\\(", ")", "\\)", ":", "\\:").Replace(durationSuffix)
					}
				}
				escapedTitle := escapeValue(s.Title, parseMode)
				sb.WriteString(fmt.Sprintf("%s%s%s\n", trackPrefix, escapedTitle, durationSuffix))
			}
			if i < len(groups)-1 {
				sb.WriteString("\n")
			}
		}
	}
	return strings.TrimSuffix(sb.String(), "\n")
}

func renderMessage(album subsonicAlbum, songs []subsonicSong) (string, string, string) {
	title := getConfigOrDefault(cfgMessageTitle, defaultTitleTemplate)
	body := getConfigOrDefault(cfgMessageBody, defaultBodyTemplate)

	title = applyTemplate(title, album, songs)
	body = applyTemplate(body, album, songs)

	imageURL := strings.TrimSpace(getConfigOrDefault(cfgImageURLTemplate, ""))
	if imageURL != "" {
		imageURL = applyTemplate(imageURL, album, songs)
	}

	if imageURL == "" && configBool(cfgUseSubsonicCoverArt) {
		generated, err := buildSubsonicCoverArtURL(album.ID)
		if err != nil {
			pdk.Log(pdk.LogWarn, fmt.Sprintf("failed to build Subsonic cover URL for album %q: %v", album.ID, err))
		} else {
			imageURL = generated
		}
	}

	return title, body, imageURL
}

func buildSubsonicCoverArtURL(albumID string) (string, error) {
	baseURL := strings.TrimRight(strings.TrimSpace(getConfigOrDefault(cfgSubsonicBaseURL, "")), "/")
	user := strings.TrimSpace(getConfigOrDefault(cfgSubsonicUser, ""))
	password := getConfigOrDefault(cfgSubsonicPassword, "")
	client := strings.TrimSpace(getConfigOrDefault(cfgSubsonicClient, defaultSubsonicClient))
	apiVersion := strings.TrimSpace(getConfigOrDefault(cfgSubsonicAPIVersion, defaultSubsonicAPI))
	coverSize := strings.TrimSpace(getConfigOrDefault(cfgSubsonicCoverSize, defaultCoverSize))

	if baseURL == "" {
		return "", fmt.Errorf("subsonic_base_url is empty")
	}
	if user == "" {
		return "", fmt.Errorf("subsonic_user is empty")
	}
	if password == "" {
		return "", fmt.Errorf("subsonic_password is empty")
	}
	if albumID == "" {
		return "", fmt.Errorf("album id is empty")
	}

	salt := albumID
	token := md5Hex(password + salt)

	values := url.Values{}
	values.Set("id", albumID)
	values.Set("size", coverSize)
	values.Set("u", user)
	values.Set("t", token)
	values.Set("s", salt)
	values.Set("v", apiVersion)
	values.Set("c", client)

	return baseURL + "/rest/getCoverArt.view?" + values.Encode(), nil
}

func md5Hex(input string) string {
	sum := md5.Sum([]byte(input))
	return hex.EncodeToString(sum[:])
}

func configBool(key string) bool {
	raw, ok := pdk.GetConfig(key)
	if !ok {
		return false
	}
	switch strings.ToLower(strings.TrimSpace(raw)) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}

func formatDuration(seconds int) string {
	if seconds <= 0 {
		return ""
	}
	h := seconds / 3600
	m := (seconds % 3600) / 60
	s := seconds % 60
	if h > 0 {
		return fmt.Sprintf("%d:%02d:%02d", h, m, s)
	}
	return fmt.Sprintf("%d:%02d", m, s)
}

func escapeMarkdown(val string) string {
	r := strings.NewReplacer(
		"_", "\\_",
		"*", "\\*",
		"`", "\\`",
		"[", "\\[",
	)
	return r.Replace(val)
}

func escapeMarkdownV2(val string) string {
	r := strings.NewReplacer(
		"_", "\\_",
		"*", "\\*",
		"[", "\\[",
		"]", "\\]",
		"(", "\\(",
		")", "\\)",
		"~", "\\~",
		"`", "\\`",
		">", "\\>",
		"#", "\\#",
		"+", "\\+",
		"-", "\\-",
		"=", "\\=",
		"|", "\\|",
		"{", "\\{",
		"}", "\\}",
		".", "\\.",
		"!", "\\!",
	)
	return r.Replace(val)
}

func escapeHTML(val string) string {
	r := strings.NewReplacer(
		"&", "&amp;",
		"<", "&lt;",
		">", "&gt;",
	)
	return r.Replace(val)
}

func escapeValue(val string, parseMode string) string {
	switch strings.ToLower(parseMode) {
	case "markdown":
		return escapeMarkdown(val)
	case "markdownv2":
		return escapeMarkdownV2(val)
	case "html":
		return escapeHTML(val)
	default:
		return val
	}
}

func applyTemplate(input string, album subsonicAlbum, songs []subsonicSong) string {
	yearStr := ""
	if album.Year > 0 {
		yearStr = fmt.Sprintf("%d", album.Year)
	}
	songCountStr := ""
	if album.SongCount > 0 {
		songCountStr = fmt.Sprintf("%d", album.SongCount)
	}
	durationStr := formatDuration(album.Duration)

	baseURL := strings.TrimRight(strings.TrimSpace(getConfigOrDefault(cfgSubsonicBaseURL, "")), "/")
	albumURL := ""
	if baseURL != "" {
		albumURL = baseURL + "/#/album/" + album.ID
	}

	replacer := strings.NewReplacer(
		"{album}", album.Name,
		"{artist}", album.Artist,
		"{id}", album.ID,
		"{artistId}", album.ArtistID,
		"{year}", yearStr,
		"{genre}", album.Genre,
		"{discCount}", fmt.Sprintf("%d", getDiscCount(songs)),
		"{songCount}", songCountStr,
		"{duration}", durationStr,
		"{songs}", formatSongList(songs),
		"{url}", albumURL,
	)
	return replacer.Replace(input)
}

func cleanHTMLTemplate(input string) string {
	// 1. Unescape literal \n and quotes
	input = strings.ReplaceAll(input, "\\n", "\n")
	input = strings.ReplaceAll(input, "\\\"", "\"")

	// 2. Process newlines: convert single newlines between block elements or tags to space/nothing
	// We handle:
	// - Newline between '>' and '<': drop entirely
	// - Newline after '</h4>', '</h3>', '</h2>', '</h1>', '</div>', '</p>', '</table>': drop entirely
	// - Newline before '<table>', '<div>', '<p>', '<h2>', '<h3>', '<h4>': drop entirely
	// - Newline within normal text: convert to <br/>

	lines := strings.Split(input, "\n")
	var cleanedLines []string

	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if trimmed != "" {
			cleanedLines = append(cleanedLines, trimmed)
		}
	}

	// Join with nothing if adjacent to HTML tags, otherwise join
	var result strings.Builder
	for i, l := range cleanedLines {
		result.WriteString(l)
		if i < len(cleanedLines)-1 {
			next := cleanedLines[i+1]
			// If current line ends with a tag or next line starts with a tag, don't add <br/>
			if strings.HasSuffix(l, ">") || strings.HasPrefix(next, "<") {
				// No break tag needed between HTML elements
			} else {
				result.WriteString("<br/>")
			}
		}
	}

	return result.String()
}

func applyTemplateTelegram(input string, album subsonicAlbum, songs []subsonicSong, parseMode string) string {
	yearStr := ""
	if album.Year > 0 {
		yearStr = fmt.Sprintf("%d", album.Year)
	}
	songCountStr := ""
	if album.SongCount > 0 {
		songCountStr = fmt.Sprintf("%d", album.SongCount)
	}
	durationStr := formatDuration(album.Duration)

	baseURL := strings.TrimRight(strings.TrimSpace(getConfigOrDefault(cfgSubsonicBaseURL, "")), "/")
	albumURL := ""
	if baseURL != "" {
		albumURL = baseURL + "/#/album/" + album.ID
	}

	replacer := strings.NewReplacer(
		"{album}", escapeValue(album.Name, parseMode),
		"{artist}", escapeValue(album.Artist, parseMode),
		"{id}", escapeValue(album.ID, parseMode),
		"{artistId}", escapeValue(album.ArtistID, parseMode),
		"{year}", escapeValue(yearStr, parseMode),
		"{genre}", escapeValue(album.Genre, parseMode),
		"{discCount}", escapeValue(fmt.Sprintf("%d", getDiscCount(songs)), parseMode),
		"{songCount}", escapeValue(songCountStr, parseMode),
		"{duration}", escapeValue(durationStr, parseMode),
		"{songs}", formatSongListTelegram(songs, parseMode),
		"{url}", escapeValue(albumURL, parseMode),
	)

	output := replacer.Replace(input)

	if strings.ToLower(parseMode) == "html" {
		output = cleanHTMLTemplate(output)
	}

	return output
}

func getConfigOrDefault(key, fallback string) string {
	if v, ok := pdk.GetConfig(key); ok && strings.TrimSpace(v) != "" {
		return v
	}
	return fallback
}

func sendTelegramRichMessage(apiBase, chatID string, threadID int, htmlContent string, imgBytes []byte) (*host.HTTPResponse, error) {
	var b bytes.Buffer
	w := multipart.NewWriter(&b)

	if err := w.WriteField("chat_id", chatID); err != nil {
		return nil, err
	}
	if threadID > 0 {
		if err := w.WriteField("message_thread_id", fmt.Sprintf("%d", threadID)); err != nil {
			return nil, err
		}
	}

	rm := richMessage{
		HTML: htmlContent,
	}

	if len(imgBytes) > 0 {
		rm.Media = []richMessageMedia{
			{
				ID: "cover",
				Media: richMessageMediaDetails{
					Type:  "photo",
					Media: "attach://cover",
				},
			},
		}

		fw, err := w.CreateFormFile("cover", "cover.png")
		if err != nil {
			return nil, err
		}
		if _, err := fw.Write(imgBytes); err != nil {
			return nil, err
		}
	}

	rmBytes, err := json.Marshal(rm)
	if err != nil {
		return nil, err
	}

	if err := w.WriteField("rich_message", string(rmBytes)); err != nil {
		return nil, err
	}

	if err := w.Close(); err != nil {
		return nil, err
	}

	return host.HTTPSend(host.HTTPRequest{
		Method:    "POST",
		URL:       apiBase + "/sendRichMessage",
		Headers:   map[string]string{"Content-Type": w.FormDataContentType()},
		Body:      b.Bytes(),
		TimeoutMs: 25000,
	})
}

func sendTelegramDirect(botToken, chatIDConfig, title, body, imageURL string, album subsonicAlbum, songs []subsonicSong) error {
	globalThreadIDStr := strings.TrimSpace(getConfigOrDefault(cfgTelegramThreadID, ""))

	var globalThreadID int
	if globalThreadIDStr != "" {
		var err error
		globalThreadID, err = strconv.Atoi(globalThreadIDStr)
		if err != nil {
			pdk.Log(pdk.LogWarn, fmt.Sprintf("invalid telegram_thread_id: %s, ignoring", globalThreadIDStr))
		}
	}

	apiBase := "https://api.telegram.org/bot" + botToken

	// Re-render templates specifically for Telegram using HTML escaping
	titleTemplate := getConfigOrDefault(cfgMessageTitle, defaultTitleTemplate)
	bodyTemplate := getConfigOrDefault(cfgMessageBody, defaultBodyTemplate)

	telegramTitle := applyTemplateTelegram(titleTemplate, album, songs, "html")
	telegramBody := applyTemplateTelegram(bodyTemplate, album, songs, "html")

	htmlContent := strings.TrimSpace(telegramTitle)
	if htmlContent != "" && strings.TrimSpace(telegramBody) != "" {
		htmlContent += telegramBody
	} else if htmlContent == "" {
		htmlContent = telegramBody
	}

	// Auto-inject image tag if image exists and is not already referenced in HTML
	if imageURL != "" && !strings.Contains(htmlContent, "tg://photo?id=cover") {
		htmlContent = "<img src=\"tg://photo?id=cover\" />" + htmlContent
	}

	// Split chat IDs by comma to support sending to multiple chats
	chatIDs := strings.Split(chatIDConfig, ",")
	for _, rawChatID := range chatIDs {
		targetChatID := strings.TrimSpace(rawChatID)
		if targetChatID == "" {
			continue
		}

		targetThreadID := globalThreadID
		// Support individual thread ID via format "chatID:threadID"
		if strings.Contains(targetChatID, ":") {
			parts := strings.SplitN(targetChatID, ":", 2)
			targetChatID = strings.TrimSpace(parts[0])
			threadStr := strings.TrimSpace(parts[1])
			if threadStr != "" {
				var err error
				targetThreadID, err = strconv.Atoi(threadStr)
				if err != nil {
					pdk.Log(pdk.LogWarn, fmt.Sprintf("invalid thread ID in chat ID %q: %s, ignoring", rawChatID, threadStr))
					targetThreadID = 0
				}
			} else {
				targetThreadID = 0
			}
		}

		pdk.Log(pdk.LogInfo, fmt.Sprintf("sending Telegram rich notification to chat=%s thread=%d", targetChatID, targetThreadID))

		var imgBytes []byte
		if imageURL != "" {
			pdk.Log(pdk.LogInfo, fmt.Sprintf("descargando portada del album para subirla a Telegram: %s", imageURL))
			imgResp, err := host.HTTPSend(host.HTTPRequest{
				Method:    "GET",
				URL:       imageURL,
				TimeoutMs: 15000,
			})
			if err == nil && imgResp.StatusCode >= 200 && imgResp.StatusCode < 300 {
				pdk.Log(pdk.LogInfo, "portada descargada correctamente")
				imgBytes = imgResp.Body
			} else {
				statusCode := int32(-1)
				if err == nil {
					statusCode = imgResp.StatusCode
				}
				pdk.Log(pdk.LogWarn, fmt.Sprintf("no se pudo descargar la portada (status=%d, err=%v); enviando sin portada...", statusCode, err))
			}
		}

		resp, err := sendTelegramRichMessage(apiBase, targetChatID, targetThreadID, htmlContent, imgBytes)
		if err != nil {
			pdk.Log(pdk.LogError, fmt.Sprintf("telegram sendRichMessage API error: %v", err))
			continue
		}

		if resp.StatusCode >= 200 && resp.StatusCode < 300 {
			pdk.Log(pdk.LogInfo, fmt.Sprintf("telegram rich message sent successfully for %q by %q to chat %s", album.Name, album.Artist, targetChatID))
		} else {
			pdk.Log(pdk.LogError, fmt.Sprintf("telegram sendRichMessage returned status %d for chat %s (body: %q)", resp.StatusCode, targetChatID, string(resp.Body)))
		}
	}

	return nil
}

func loadLastSeenID() string {
	value, exists, err := host.KVStoreGet(kvLastSeenAlbum)
	if err != nil {
		pdk.Log(pdk.LogWarn, fmt.Sprintf("kvstore get error: %v", err))
		return ""
	}
	if !exists {
		return ""
	}
	return string(value)
}

func saveLastSeenID(id string) error {
	return host.KVStoreSet(kvLastSeenAlbum, []byte(id))
}

func main() {}
