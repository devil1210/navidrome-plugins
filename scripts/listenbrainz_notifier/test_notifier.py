#!/usr/bin/env python3
"""
Unit and Integration Tests for ListenBrainz Missing Tracks Notifier
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from notifier import (
    DownloadJob,
    DownloadQueue,
    ListenBrainzEnricher,
    MissingTrack,
    MusicDownloader,
    NavidromeClient,
    ResolutionResult,
    ResolvedAlbum,
    ResolvedTrack,
    StateTracker,
    TelegramBotListener,
    TelegramSender,
    TrackParser,
    UniversalResolver,
    YouTubeMusicDiscoverer,
    parse_sync_args,
)


class TestTrackParser(unittest.TestCase):

    def setUp(self):
        self.sample_comment = (
            "Imported from playlist https://listenbrainz.org/playlist/8f0e309a-2002-47b0-81a7-fb787fb12e03\n"
            "Updated on: 2026-08-24T00:52:31Z\n"
            "Tracks not matched Three Little Birds by Bob Marley & The Wailers, "
            "ray (超かぐや姫! Version) by かぐや(cv.夏吉ゆうこ) & 月見ヤチヨ(cv.早見沙織), "
            "I’m Just a Kid by Simple Plan, "
            "Boom, Boom, Boom, Boom!! by Vengaboys, "
            "Paint It Black by The Rolling Stones, "
            "Rasputin by Boney M., "
            "Another Day in Paradise by Phil Collins, "
            "Mr. Brightside by The Killers, "
            "Poker Face by Lady Gaga, "
            "Harder, Better, Faster, Stronger by Daft Punk, "
            "Wait and Bleed by Slipknot, "
            "Monster by Skillet, "
            "Tainted Love by Soft Cell, "
            "Re:Re: by ASIAN KUNG-FU GENERATION, "
            "Cum On Feel the Noize by Quiet Riot, "
            "This Love by Maroon 5, "
            "Hungry Like the Wolf by Duran Duran, "
            "Show Me How to Live by Audioslave, "
            "No More Tears by Ozzy Osbourne, "
            "Aquí no es así by Caifanes, "
            "Space Oddity by David Bowie, "
            "Don’t Stop Believin’ by Journey, "
            "The Rumbling by SiM, "
            "I Wanna Be Your Slave by Måneskin, "
            "Kryptonite by 3 Doors Down, "
            "All the Things She Said by t.A.T.u., "
            "Ironic by Alanis Morissette, "
            "I Hate Everything About You by Three Days Grace\n"
            "Tracks excluded by rating rule: Something by Someone"
        )

    def test_extract_missing_text(self):
        extracted = TrackParser.extract_missing_text(self.sample_comment)
        self.assertIsNotNone(extracted)
        self.assertTrue(extracted.startswith("Three Little Birds by Bob Marley"))
        # Verify it didn't include the 'Tracks excluded by rating rule' line
        self.assertNotIn("Tracks excluded by rating rule", extracted)
        self.assertTrue(extracted.endswith("Three Days Grace"))

    def test_parse_all_28_tracks(self):
        extracted = TrackParser.extract_missing_text(self.sample_comment)
        tracks = TrackParser.parse_missing_tracks(extracted)

        self.assertEqual(len(tracks), 28)

        # Verify specific tricky tracks
        self.assertEqual(tracks[0].title, "Three Little Birds")
        self.assertEqual(tracks[0].artist, "Bob Marley & The Wailers")

        # Track 2: Japanese characters
        self.assertEqual(tracks[1].title, "ray (超かぐや姫! Version)")
        self.assertEqual(tracks[1].artist, "かぐや(cv.夏吉ゆうこ) & 月見ヤチヨ(cv.早見沙織)")

        # Track 3: Simple Plan
        self.assertEqual(tracks[2].title, "I’m Just a Kid")
        self.assertEqual(tracks[2].artist, "Simple Plan")

        # Track 4: Commas inside title ("Boom, Boom, Boom, Boom!!")
        self.assertEqual(tracks[3].title, "Boom, Boom, Boom, Boom!!")
        self.assertEqual(tracks[3].artist, "Vengaboys")

        # Track 10: Commas inside title ("Harder, Better, Faster, Stronger")
        self.assertEqual(tracks[9].title, "Harder, Better, Faster, Stronger")
        self.assertEqual(tracks[9].artist, "Daft Punk")

        # Last track
        self.assertEqual(tracks[27].title, "I Hate Everything About You")
        self.assertEqual(tracks[27].artist, "Three Days Grace")

    def test_parse_generated_jams_format(self):
        gen_comment = "Jams generated on Friday\nTracks not found in library: Song A, Song B, Song C\nExcluded: 0"
        extracted = TrackParser.extract_missing_text(gen_comment)
        self.assertEqual(extracted, "Song A, Song B, Song C")
        tracks = TrackParser.parse_missing_tracks(extracted)
        self.assertEqual(len(tracks), 3)
        self.assertEqual(tracks[0].title, "Song A")
        self.assertEqual(tracks[1].title, "Song B")
        self.assertEqual(tracks[2].title, "Song C")

    def test_rebuild_comment_without_tracks(self):
        orig_comment = (
            "Imported from playlist https://listenbrainz.org/playlist/test-id\n"
            "Updated on: 2026-07-24T13:55:55Z\n"
            "Tracks not matched Song 1 by Artist A, Song 2 by Artist B, Song 3 by Artist C\n"
            "Tracks excluded by rating rule: Excluded 1"
        )
        # Remove Song 2
        updated = TrackParser.rebuild_comment_without_tracks(orig_comment, {"Song 2"})
        self.assertIn("Song 1 by Artist A, Song 3 by Artist C", updated)
        self.assertNotIn("Song 2", updated)
        self.assertIn("Imported from playlist", updated)
        self.assertIn("Tracks excluded by rating rule: Excluded 1", updated)

        # Remove all remaining tracks
        cleaned = TrackParser.rebuild_comment_without_tracks(updated, {"Song 1", "Song 3"})
        self.assertNotIn("Tracks not matched", cleaned)
        self.assertIn("Imported from playlist", cleaned)
        self.assertIn("Tracks excluded by rating rule: Excluded 1", cleaned)



class TestTelegramSender(unittest.TestCase):

    def setUp(self):
        self.sender_bq = TelegramSender(bot_token="test", chat_id="123", quote_style="blockquote")
        self.sender_code = TelegramSender(bot_token="test", chat_id="123", quote_style="code")

    def test_format_rich_html(self):
        res = ResolutionResult(
            albums=[
                ResolvedAlbum(
                    url="https://music.youtube.com/playlist?list=OLAK5uy_test1",
                    album_name="Exodus",
                    artist_name="Bob Marley",
                    tracks=["Three Little Birds"],
                )
            ],
            tracks=[
                ResolvedTrack(
                    url="https://music.youtube.com/watch?v=12345",
                    title="Some Single",
                    artist="Some Artist",
                )
            ],
        )
        msgs = self.sender_bq.format_rich_html("Daily Jams", res)
        self.assertEqual(len(msgs), 1)
        self.assertIn("<details>", msgs[0])
        self.assertIn("<summary>📥 Enlaces para MediaHuman (2)</summary>", msgs[0])
        self.assertIn('<pre><code class="language-copy">', msgs[0])
        self.assertIn("<summary>📋 Ver Detalle de Álbumes (1)</summary>", msgs[0])
        self.assertIn("<table bordered striped>", msgs[0])
        self.assertIn("https://music.youtube.com/playlist?list=OLAK5uy_test1", msgs[0])
        self.assertIn("https://music.youtube.com/watch?v=12345", msgs[0])
        self.assertIn("<b>Bob Marley</b> — <b>Exodus</b>", msgs[0])


class TestStateTracker(unittest.TestCase):

    def test_state_saving_and_loading(self, tmp_path=Path("./test_state.json")):
        tracker = StateTracker(tmp_path)
        try:
            self.assertFalse(tracker.is_already_processed("pls_1", "comment A"))
            tracker.mark_processed("pls_1", "comment A", "2026-08-24T00:00:00Z")
            self.assertTrue(tracker.is_already_processed("pls_1", "comment A"))
            self.assertFalse(tracker.is_already_processed("pls_1", "comment B (new tracks)"))
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


class TestTelegramBotListener(unittest.TestCase):

    def setUp(self):
        self.mock_sync = MagicMock()
        self.mock_status = MagicMock(return_value="Status OK")
        self.mock_download = MagicMock()
        self.mock_add = MagicMock()
        self.mock_add_disc = MagicMock()
        self.listener = TelegramBotListener(
            bot_token="test_token",
            authorized_chat_ids=["12345"],
            on_sync_command=self.mock_sync,
            on_status_command=self.mock_status,
            on_download_command=self.mock_download,
            on_add_command=self.mock_add,
            on_add_disc_callback=self.mock_add_disc,
        )
        self.listener.send_reply = MagicMock()

    def test_sync_command(self):
        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 12345},
                "text": "/sync",
            },
        }
        self.listener.handle_update(update)
        self.mock_sync.assert_called_once_with("12345", None, None)

    def test_download_command(self):
        update = {
            "update_id": 10,
            "message": {
                "chat": {"id": 12345},
                "text": "/dl 2",
            },
        }
        self.listener.handle_update(update)
        self.mock_download.assert_called_once_with("12345", None, "2")

    def test_add_command_with_args(self):
        update = {
            "update_id": 11,
            "message": {
                "chat": {"id": 12345},
                "text": "/add artista Daft Punk",
            },
        }
        with patch("threading.Thread") as mock_thread_cls:
            self.listener.handle_update(update)
            mock_thread_cls.assert_called_once()
            _, kwargs = mock_thread_cls.call_args
            self.assertEqual(kwargs.get("target"), self.mock_add)
            self.assertEqual(kwargs.get("args"), ("12345", None, "artista Daft Punk"))

    def test_add_disc_callback_dispatch(self):
        update = {
            "update_id": 15,
            "callback_query": {
                "id": "cq_999",
                "message": {
                    "message_id": 42,
                    "chat": {"id": 12345},
                },
                "data": "add_disc:albums:abc12345",
            },
        }
        self.listener.handle_update(update)
        self.mock_add_disc.assert_called_once_with("12345", None, 42, "cq_999", "add_disc:albums:abc12345")

    def test_add_command_empty_shows_help(self):
        update = {
            "update_id": 12,
            "message": {
                "chat": {"id": 12345},
                "text": "/add",
            },
        }
        self.listener.handle_update(update)
        self.mock_add.assert_not_called()
        self.listener.send_reply.assert_called_once()
        args = self.listener.send_reply.call_args[0]
        self.assertEqual(args[0], "12345")
        self.assertIn("Uso del comando /add", args[1])

    def test_sync_with_argument(self):
        update = {
            "update_id": 2,
            "message": {
                "chat": {"id": 12345},
                "text": "/sync Descubrimiento de la Semana",
            },
        }
        self.listener.handle_update(update)
        self.mock_sync.assert_called_once_with("12345", None, "Descubrimiento de la Semana")

    def test_status_command(self):
        update = {
            "update_id": 3,
            "message": {
                "chat": {"id": 12345},
                "text": "/status",
            },
        }
        self.listener.handle_update(update)
        self.mock_status.assert_called_once()
        self.listener.send_reply.assert_called_with("12345", "Status OK", None)

    def test_unauthorized_chat_ignored(self):
        update = {
            "update_id": 4,
            "message": {
                "chat": {"id": 99999},  # Unauthorized
                "text": "/sync",
            },
        }
        self.listener.handle_update(update)
        self.mock_sync.assert_not_called()

    def test_non_command_ignored(self):
        update = {
            "update_id": 5,
            "message": {
                "chat": {"id": 12345},
                "text": "Hello bot",
            },
        }
        self.listener.handle_update(update)
        self.mock_sync.assert_not_called()



class TestListenBrainzEnricher(unittest.TestCase):

    def setUp(self):
        self.enricher = ListenBrainzEnricher(username="test_user", token="test_token")
        # Prepopulate dummy title_map for testing without external HTTP calls
        self.enricher.title_map = {
            "livin' on a prayer": {"title": "Livin' on a Prayer", "artist": "Bon Jovi", "album": "Slippery When Wet"},
            "californication": {"title": "Californication", "artist": "Red Hot Chili Peppers", "album": "Californication"},
            "stand by me": {"title": "Stand by Me", "artist": "Ben E. King", "album": "Stand By Me"},
            "boulevard of broken dreams": {"title": "Boulevard of Broken Dreams", "artist": "Green Day", "album": "American Idiot"},
        }

    def test_normalize_title(self):
        self.assertEqual(self.enricher._normalize_title("Livin’ on a Prayer"), "livin' on a prayer")
        self.assertEqual(self.enricher._normalize_title("  CALIFORNICATION  "), "californication")
        self.assertEqual(self.enricher._normalize_title("“Song”"), '"song"')

    def test_enrich_tracks(self):
        tracks = [
            MissingTrack(title="Livin’ on a Prayer"),
            MissingTrack(title="Californication"),
            MissingTrack(title="Stand by Me"),
            MissingTrack(title="Unknown Song That Does Not Exist"),
            MissingTrack(title="Custom Title", artist="Existing Artist"),
        ]

        enriched = self.enricher.enrich_tracks(tracks)
        self.assertEqual(len(enriched), 5)
        self.assertEqual(enriched[0].artist, "Bon Jovi")
        self.assertEqual(enriched[1].artist, "Red Hot Chili Peppers")
        self.assertEqual(enriched[2].artist, "Ben E. King")
        self.assertEqual(enriched[3].artist, "")  # Unmatched remains empty
        self.assertEqual(enriched[4].artist, "Existing Artist")  # Untouched


class TestParseSyncArgs(unittest.TestCase):

    def test_default_empty(self):
        pls, batch, limit = parse_sync_args(None, default_limit=20)
        self.assertIsNone(pls)
        self.assertEqual(batch, 1)
        self.assertEqual(limit, 20)

    def test_playlist_only(self):
        pls, batch, limit = parse_sync_args("Descubrimiento Diario", default_limit=20)
        self.assertEqual(pls, "Descubrimiento Diario")
        self.assertEqual(batch, 1)
        self.assertEqual(limit, 20)

    def test_playlist_with_batch_number(self):
        pls, batch, limit = parse_sync_args("Descubrimiento Diario 2", default_limit=20)
        self.assertEqual(pls, "Descubrimiento Diario")
        self.assertEqual(batch, 2)
        self.assertEqual(limit, 20)

    def test_playlist_with_explicit_lote(self):
        pls, batch, limit = parse_sync_args("Descubrimiento Diario lote 3", default_limit=20)
        self.assertEqual(pls, "Descubrimiento Diario")
        self.assertEqual(batch, 3)
        self.assertEqual(limit, 20)

    def test_playlist_with_explicit_limit(self):
        pls, batch, limit = parse_sync_args("Descubrimiento Diario limit 35", default_limit=20)
        self.assertEqual(pls, "Descubrimiento Diario")
        self.assertEqual(batch, 1)
        self.assertEqual(limit, 35)

    def test_playlist_with_limit_and_batch(self):
        pls, batch, limit = parse_sync_args("Descubrimiento Diario lote 2 limit 15", default_limit=20)
        self.assertEqual(pls, "Descubrimiento Diario")
        self.assertEqual(batch, 2)
        self.assertEqual(limit, 15)

    def test_playlist_with_all(self):
        pls, batch, limit = parse_sync_args("Descubrimiento Diario all", default_limit=20)
        self.assertEqual(pls, "Descubrimiento Diario")
        self.assertEqual(batch, 1)
        self.assertEqual(limit, 0)

    def test_standalone_batch_number(self):
        pls, batch, limit = parse_sync_args("3", default_limit=20)
        self.assertIsNone(pls)
        self.assertEqual(batch, 3)
        self.assertEqual(limit, 20)


class TestBatchingAndLimits(unittest.TestCase):

    def test_state_tracker_limits(self, tmp_path=Path("./test_state_limit.json")):
        tracker = StateTracker(tmp_path)
        try:
            self.assertEqual(tracker.get_limit(default_limit=20), 20)
            tracker.set_limit(35)
            self.assertEqual(tracker.get_limit(default_limit=20), 35)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_state_tracker_batch_persistence(self, tmp_path=Path("./test_state_batches.json")):
        tracker = StateTracker(tmp_path)
        try:
            tracker.save_last_batch(
                playlist_name="Descubrimiento Diario",
                batch_number=1,
                batch_size=20,
                total_batches=3,
                albums=[{"url": "https://music.youtube.com/playlist?list=1", "album_name": "Album 1", "artist_name": "Artist 1"}],
                tracks=[{"url": "https://music.youtube.com/watch?v=1", "title": "Track 1", "artist": "Artist 1"}],
            )
            batch = tracker.get_last_batch("Descubrimiento Diario")
            self.assertIsNotNone(batch)
            self.assertEqual(batch["batch_number"], 1)
            self.assertEqual(len(batch["albums"]), 1)
            self.assertEqual(batch["albums"][0]["album_name"], "Album 1")

            latest = tracker.get_last_batch()
            self.assertIsNotNone(latest)
            self.assertEqual(latest["playlist_name"], "Descubrimiento Diario")
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_format_rich_html_with_batches(self):
        sender = TelegramSender(bot_token="test", chat_id="123")
        res = ResolutionResult(
            albums=[
                ResolvedAlbum(
                    url="https://music.youtube.com/playlist?list=OLAK5uy_1",
                    album_name="Album 1",
                    artist_name="Artist 1",
                    tracks=["Track 1", "Track 2"],
                )
            ],
            total_albums_detected=50,
            batch_number=1,
            total_batches=3,
            batch_size=20,
        )
        msgs = sender.format_rich_html("Descubrimiento Diario", res)
        self.assertIn("Lote Actual", msgs[0])
        self.assertIn("1 de 3", msgs[0])
    def test_sorting_by_track_count_and_appearance_index(self):
        # Album A has 1 track, appeared at index 0 in playlist
        alb_a = ResolvedAlbum(url="url_a", album_name="Album A", artist_name="Artist Z", tracks=["Track 1"], first_index=0)
        # Album B has 2 tracks, appeared at index 5 in playlist
        alb_b = ResolvedAlbum(url="url_b", album_name="Album B", artist_name="Artist Y", tracks=["Track 6", "Track 7"], first_index=5)
        # Album C has 1 track, appeared at index 2 in playlist
        alb_c = ResolvedAlbum(url="url_c", album_name="Album C", artist_name="Artist A", tracks=["Track 3"], first_index=2)

        albums = [alb_a, alb_b, alb_c]
        albums.sort(key=lambda a: (-len(a.tracks), a.first_index))

        # Expected:
        # 1. alb_b (2 tracks)
        # 2. alb_a (1 track, appeared at index 0)
        # 3. alb_c (1 track, appeared at index 2)
        self.assertEqual(albums[0], alb_b)
        self.assertEqual(albums[1], alb_a)
        self.assertEqual(albums[2], alb_c)


class TestNavidromeClient(unittest.TestCase):

    def setUp(self):
        self.client = NavidromeClient("http://localhost:4533", "admin", "secret")

    @patch.object(NavidromeClient, "get_playlist_details")
    @patch("requests.get")
    def test_add_tracks_to_playlist_deduplicates_and_skips_existing(self, mock_get, mock_details):
        # Existing playlist has track1 and track2
        mock_details.return_value = {
            "entry": [
                {"id": "track1", "title": "Track 1"},
                {"id": "track2", "title": "Track 2"},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"subsonic-response": {"status": "ok"}}
        mock_get.return_value = mock_resp

        # We try to add track1 (exists), track3, track3 (duplicate), track4
        success = self.client.add_tracks_to_playlist("pls_1", ["track1", "track3", "track3", "track4"])
        self.assertTrue(success)

        # Ensure requests.get was called with ONLY track3 and track4 once each
        mock_get.assert_called_once()
        called_url = mock_get.call_args[0][0]
        self.assertIn("songIdToAdd=track3", called_url)
        self.assertIn("songIdToAdd=track4", called_url)
        self.assertNotIn("songIdToAdd=track1", called_url)
        self.assertEqual(called_url.count("songIdToAdd=track3"), 1)

    @patch.object(NavidromeClient, "get_playlist_details")
    @patch("requests.get")
    def test_add_tracks_to_playlist_all_exist(self, mock_get, mock_details):
        mock_details.return_value = {
            "entry": [
                {"id": "track1"},
                {"id": "track2"},
            ]
        }
        # Both tracks already exist
        success = self.client.add_tracks_to_playlist("pls_1", ["track1", "track2", "track1"])
        self.assertTrue(success)
        # Should not make any update network call
        mock_get.assert_not_called()

    @patch("requests.get")
    def test_set_playlist_tracks(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"subsonic-response": {"status": "ok"}}
        mock_get.return_value = mock_resp

        success = self.client.set_playlist_tracks("pls_1", ["t1", "t2", "t2"])
        self.assertTrue(success)
        mock_get.assert_called_once()
        called_url = mock_get.call_args[0][0]
        self.assertIn("createPlaylist.view", called_url)
        self.assertIn("playlistId=pls_1", called_url)
        self.assertIn("songId=t1", called_url)
        self.assertIn("songId=t2", called_url)
        self.assertEqual(called_url.count("songId=t2"), 1)

    @patch("requests.get")
    def test_get_album_tracks(self, mock_get):
        # 1. Test album with list of songs
        mock_resp_list = MagicMock()
        mock_resp_list.json.return_value = {
            "subsonic-response": {
                "status": "ok",
                "album": {
                    "id": "alb_1",
                    "name": "Discovery",
                    "song": [
                        {"id": "s1", "title": "One More Time", "track": 1},
                        {"id": "s2", "title": "Aerodynamic", "track": 2},
                    ],
                },
            }
        }
        mock_get.return_value = mock_resp_list
        tracks = self.client.get_album_tracks("alb_1")
        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0]["title"], "One More Time")

        # 2. Test album with single song dict
        mock_resp_dict = MagicMock()
        mock_resp_dict.json.return_value = {
            "subsonic-response": {
                "status": "ok",
                "album": {
                    "id": "alb_single",
                    "song": {"id": "s_single", "title": "Single Track", "track": 1},
                },
            }
        }
        mock_get.return_value = mock_resp_dict
        tracks_single = self.client.get_album_tracks("alb_single")
        self.assertEqual(len(tracks_single), 1)
        self.assertEqual(tracks_single[0]["title"], "Single Track")

        # 3. Test album not found
        mock_resp_none = MagicMock()
        mock_resp_none.json.return_value = {"subsonic-response": {"status": "ok"}}
        mock_get.return_value = mock_resp_none
        self.assertEqual(self.client.get_album_tracks("alb_none"), [])

    def test_find_best_match_track_number_prefix_and_version_tag(self):
        # In Navidrome: "01 - Hai Yorokonde (English ver)"
        self.client.search_songs = MagicMock(return_value=[
            {
                "id": "song_hai",
                "title": "01 - Hai Yorokonde (English ver)",
                "artist": "Kocchi no Kento",
                "album": "Hai Yorokonde (single)",
            }
        ])
        # In playlist: "Hai Yorokonde - English ver"
        match = self.client.find_best_match("Hai Yorokonde - English ver", "Kocchi no Kento")
        self.assertIsNotNone(match)
        self.assertEqual(match["id"], "song_hai")

    def test_find_best_match_contributors(self):
        # In Navidrome: Wham! with George Michael in contributors
        self.client.search_songs = MagicMock(return_value=[
            {
                "id": "song_careless",
                "title": "Careless Whisper",
                "artist": "Wham!",
                "contributors": [{"role": "producer", "artist": {"name": "George Michael"}}],
                "album": "Make It Big",
            }
        ])
        match = self.client.find_best_match("Careless Whisper", "George Michael")
        self.assertIsNotNone(match)
        self.assertEqual(match["id"], "song_careless")

    def test_find_best_match_romaji_japanese(self):
        # In Navidrome: "Hachikō" by "Fujii Kaze"
        self.client.search_songs = MagicMock(return_value=[
            {
                "id": "song_hachiko",
                "title": "Hachikō",
                "artist": "Fujii Kaze",
                "album": "Prema",
            }
        ])
        # In ListenBrainz: Japanese "ハチ公"
        match = self.client.find_best_match("ハチ公", "Fujii Kaze")
        self.assertIsNotNone(match)
        self.assertEqual(match["id"], "song_hachiko")

    def test_find_best_match_exact_title_with_differing_enricher_artist(self):
        # In Navidrome: "Crime and Punishment" by "Akira Senju"
        # In ListenBrainz (enriched): "Crime & Punishment" by "C.R.I.M."
        self.client.search_songs = MagicMock(return_value=[
            {
                "id": "song_crime_akira",
                "title": "Crime and Punishment",
                "artist": "Akira Senju",
                "album": "FULLMETAL ALCHEMIST Original Soundtrack 3",
            }
        ])
        match = self.client.find_best_match("Crime & Punishment", "C.R.I.M.")
        self.assertIsNotNone(match)
        self.assertEqual(match["id"], "song_crime_akira")

    def test_find_best_match_word_stem_romance(self):
        # In Navidrome: "大正浪漫 - Taishourouman" by "YOASOBI"
        # In ListenBrainz: "Romance" by "YOASOBI"
        self.client.search_songs = MagicMock(return_value=[
            {
                "id": "song_yoasobi_romance",
                "title": "大正浪漫 - Taishourouman",
                "artist": "YOASOBI",
                "album": "THE BOOK 2",
            }
        ])
        match = self.client.find_best_match("Romance", "YOASOBI")
        self.assertIsNotNone(match)
        self.assertEqual(match["id"], "song_yoasobi_romance")


class TestMusicDownloader(unittest.TestCase):

    def setUp(self):
        self.downloader = MusicDownloader(download_dir="./test_downloads")

    def test_sanitize_name(self):
        self.assertEqual(MusicDownloader.sanitize_name("AC/DC"), "AC_DC")
        self.assertEqual(MusicDownloader.sanitize_name('Album: "Best of" *Special*?'), "Album_ _Best of_ _Special__")
        self.assertEqual(MusicDownloader.sanitize_name("   Valid Album Name.  "), "Valid Album Name")
        self.assertEqual(MusicDownloader.sanitize_name(""), "Unknown")

    def test_get_ydl_opts_album(self, tmp_path=Path("./test_album_opts")):
        opts = self.downloader.get_ydl_opts(tmp_path, is_playlist=True)
        self.assertEqual(opts["format"], "ba[ext=m4a]/ba")
        self.assertFalse(opts["writethumbnail"])
        self.assertFalse(opts["allow_playlist_files"])
        self.assertTrue(any(p.get("key") == "FFmpegMetadata" for p in opts["postprocessors"]))
        self.assertFalse(any(p.get("key") == "EmbedThumbnail" for p in opts["postprocessors"]))
        self.assertIn("js_runtimes", opts)
        self.assertIn("%(playlist_index)02d", opts["outtmpl"])

        if tmp_path.exists():
            tmp_path.rmdir()

    def test_cleanup_loose_images(self, tmp_path=Path("./test_cleanup_dir")):
        tmp_path.mkdir(parents=True, exist_ok=True)
        img1 = tmp_path / "00 - NA - Album - Test.jpg"
        img2 = tmp_path / "cover.webp"
        img3 = tmp_path / "art.png"
        audio = tmp_path / "01 - Artist - Song.m4a"
        for f in (img1, img2, img3, audio):
            f.write_text("test_content")

        MusicDownloader._cleanup_loose_images(tmp_path)

        self.assertFalse(img1.exists())
        self.assertFalse(img2.exists())
        self.assertFalse(img3.exists())
        self.assertTrue(audio.exists())

        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)

    def test_get_ydl_opts_single_track(self, tmp_path=Path("./test_track_opts")):
        opts = self.downloader.get_ydl_opts(tmp_path, is_playlist=False)
        self.assertEqual(opts["format"], "ba[ext=m4a]/ba")
        self.assertNotIn("%(playlist_index)02d", opts["outtmpl"])
        self.assertIn("%(title)s", opts["outtmpl"])

        if tmp_path.exists():
            tmp_path.rmdir()

    @patch("yt_dlp.YoutubeDL")
    def test_download_album_mocked(self, mock_ydl_cls, tmp_path=Path("./test_mock_alb")):
        mock_ydl_instance = MagicMock()
        mock_ydl_cls.return_value.__enter__.return_value = mock_ydl_instance
        def fake_download(urls):
            target_file = tmp_path / "American Idiot" / "01 - Green Day - American Idiot.m4a"
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text("audio data")
        mock_ydl_instance.download.side_effect = fake_download

        downloader = MusicDownloader(download_dir=str(tmp_path))
        album = ResolvedAlbum(
            url="https://music.youtube.com/playlist?list=OLAK5uy_test",
            album_name="American Idiot",
            artist_name="Green Day",
        )
        success, folder = downloader.download_album(album)
        self.assertTrue(success)
        self.assertIn("American Idiot", folder)
        mock_ydl_instance.download.assert_called_once_with(["https://music.youtube.com/playlist?list=OLAK5uy_test"])

        if Path(folder).exists():
            shutil.rmtree(folder, ignore_errors=True)
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)

    @patch("yt_dlp.YoutubeDL")
    def test_download_album_fails_if_no_audio(self, mock_ydl_cls, tmp_path=Path("./test_mock_fail")):
        mock_ydl_instance = MagicMock()
        mock_ydl_cls.return_value.__enter__.return_value = mock_ydl_instance
        def fake_download(urls):
            target_file = tmp_path / "American Idiot" / "00 - NA - Album.jpg"
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text("image data")
        mock_ydl_instance.download.side_effect = fake_download

        downloader = MusicDownloader(download_dir=str(tmp_path))
        album = ResolvedAlbum(
            url="https://music.youtube.com/playlist?list=OLAK5uy_test",
            album_name="American Idiot",
            artist_name="Green Day",
        )
        success, folder_or_err = downloader.download_album(album)
        self.assertFalse(success)
        self.assertIn("No se descargó ninguna pista", folder_or_err)
        self.assertFalse((tmp_path / "American Idiot").exists())

    @patch("yt_dlp.YoutubeDL")
    def test_cookie_rotation_and_retry(self, mock_ydl_cls, tmp_path=Path("./test_mock_rotate")):
        c1 = tmp_path / "c1.txt"
        c2 = tmp_path / "c2.txt"
        tmp_path.mkdir(parents=True, exist_ok=True)
        c1.write_text("cookie1")
        c2.write_text("cookie2")

        downloader = MusicDownloader(download_dir=str(tmp_path), cookies_list=[str(c1), str(c2)])
        self.assertEqual(len(downloader.cookies_list), 2)
        self.assertEqual(downloader.get_current_cookie(), str(c1))

        # Rotate
        next_c = downloader.rotate_cookie()
        self.assertEqual(next_c, str(c2))
        self.assertEqual(downloader.get_current_cookie(), str(c2))

        # Rotate back
        next_c2 = downloader.rotate_cookie()
        self.assertEqual(next_c2, str(c1))

        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)

    @patch.object(NavidromeClient, "search_albums")
    def test_find_album_match(self, mock_search):
        client = NavidromeClient("http://localhost:4533", "user", "pass")
        mock_search.return_value = [
            {"id": "alb_1", "name": "Catdays (single)", "artist": "suis"},
            {"id": "alb_2", "name": "Elma", "artist": "Yorushika"},
        ]

        # Match with token overlap in artist
        res = client.find_album_match("Catdays", "suis from Yorushika")
        self.assertIsNotNone(res)
        self.assertEqual(res["id"], "alb_1")

        # Match exact
        res2 = client.find_album_match("Elma", "Yorushika")
        self.assertIsNotNone(res2)
        self.assertEqual(res2["id"], "alb_2")

        # Non-matching
        res3 = client.find_album_match("Nonexistent Album", "Some Artist")
        self.assertIsNone(res3)

    @patch.object(NavidromeClient, "search_songs")
    def test_find_best_match_artist_in_cand_title(self, mock_search):
        client = NavidromeClient("http://localhost:4533", "user", "pass")
        # Candidate has artist name inside title, while candidate artist field is romanized
        mock_search.return_value = [
            {
                "id": "song_1",
                "title": "なとり - Overdose",
                "artist": "natori",
                "album": "Overdose (single)",
            }
        ]
        res = client.find_best_match("Overdose", "なとり")
        self.assertIsNotNone(res)
        self.assertEqual(res["id"], "song_1")

    @patch("yt_dlp.YoutubeDL")
    def test_download_album_skips_if_already_on_disk(self, mock_ydl_cls, tmp_path=Path("./test_disk_skip")):
        tmp_path.mkdir(parents=True, exist_ok=True)
        album_folder = tmp_path / "Catdays"
        album_folder.mkdir(parents=True, exist_ok=True)
        # Create an existing valid audio file (> 200 KB)
        (album_folder / "01 - suis - Catdays.m4a").write_bytes(b"0" * (300 * 1024))

        downloader = MusicDownloader(download_dir=str(tmp_path))
        album = ResolvedAlbum(
            url="https://music.youtube.com/playlist?list=OLAK5uy_test",
            album_name="Catdays",
            artist_name="suis from Yorushika",
        )
        success, folder_or_err = downloader.download_album(album)
        self.assertTrue(success)
        self.assertEqual(folder_or_err, str(album_folder))
        # YoutubeDL must NOT have been called because album is complete and valid
        mock_ydl_cls.assert_not_called()

        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)

    def test_verify_album_integrity(self, tmp_path=Path("./test_integrity_check")):
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)
        tmp_path.mkdir(parents=True, exist_ok=True)

        try:
            # 1. Empty folder
            empty_dir = tmp_path / "empty"
            empty_dir.mkdir(parents=True, exist_ok=True)
            ok, _, msg = MusicDownloader.verify_album_integrity(empty_dir)
            self.assertFalse(ok)
            self.assertIn("No contiene pistas", msg)

            # 2. Folder with .part file
            part_dir = tmp_path / "part_album"
            part_dir.mkdir(parents=True, exist_ok=True)
            (part_dir / "01 - track.m4a").write_bytes(b"0" * (300 * 1024))
            (part_dir / "02 - track.m4a.part").write_bytes(b"0" * 100)
            ok, _, msg = MusicDownloader.verify_album_integrity(part_dir)
            self.assertFalse(ok)
            self.assertIn("incompleta", msg)

            # 3. Folder with tiny corrupt file (< 200 KB)
            corrupt_dir = tmp_path / "corrupt_album"
            corrupt_dir.mkdir(parents=True, exist_ok=True)
            (corrupt_dir / "01 - track.m4a").write_bytes(b"0" * 50)
            ok, _, msg = MusicDownloader.verify_album_integrity(corrupt_dir)
            self.assertFalse(ok)
            self.assertIn("truncada", msg)

            # 4. Folder with healthy complete album
            healthy_dir = tmp_path / "healthy_album"
            healthy_dir.mkdir(parents=True, exist_ok=True)
            (healthy_dir / "01 - artist - song1.m4a").write_bytes(b"0" * (300 * 1024))
            (healthy_dir / "02 - artist - song2.m4a").write_bytes(b"0" * (400 * 1024))
            ok, count, msg = MusicDownloader.verify_album_integrity(healthy_dir)
            self.assertTrue(ok)
            self.assertEqual(count, 2)
            self.assertIn("integro", msg.lower().replace("í", "i"))
        finally:
            if tmp_path.exists():
                shutil.rmtree(tmp_path, ignore_errors=True)

    @patch("yt_dlp.YoutubeDL")
    def test_get_album_tracklist(self, mock_ydl_cls):
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {
            "entries": [
                {"title": "One More Time", "id": "vid1"},
                {"title": "Aerodynamic", "id": "vid2"},
                {"title": "Digital Love", "id": "vid3"},
            ]
        }

        album = ResolvedAlbum(
            url="https://music.youtube.com/playlist?list=OLAK5uy_test",
            album_name="Discovery",
            artist_name="Daft Punk",
        )
        tracks = self.downloader.get_album_tracklist(album)
        self.assertEqual(len(tracks), 3)
        self.assertEqual(tracks[0]["index"], 1)
        self.assertEqual(tracks[0]["title"], "One More Time")
        self.assertEqual(tracks[1]["index"], 2)
        self.assertEqual(tracks[1]["title"], "Aerodynamic")

    @patch("yt_dlp.YoutubeDL")
    def test_download_album_tracks_playlist_items(self, mock_ydl_cls, tmp_path=Path("./test_partial_dl")):
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)
        tmp_path.mkdir(parents=True, exist_ok=True)

        try:
            downloader = MusicDownloader(download_dir=tmp_path)
            mock_ydl = MagicMock()
            mock_ydl_cls.return_value.__enter__.return_value = mock_ydl

            def side_effect_download(urls):
                folder = tmp_path / "Discovery"
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "02 - Daft Punk - Aerodynamic.m4a").write_bytes(b"0" * 1024)

            mock_ydl.download.side_effect = side_effect_download

            album = ResolvedAlbum(
                url="https://music.youtube.com/playlist?list=OLAK5uy_test",
                album_name="Discovery",
                artist_name="Daft Punk",
            )
            success, folder = downloader.download_album_tracks(album, [2, 5])
            self.assertTrue(success)

            # Check that playlist_items was set in opts
            call_opts = mock_ydl_cls.call_args[0][0]
            self.assertEqual(call_opts.get("playlist_items"), "2,5")
        finally:
            if tmp_path.exists():
                shutil.rmtree(tmp_path, ignore_errors=True)

    def test_process_album_cover_crops_non_square(self, tmp_path=Path("./test_cover_crop")):
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            from PIL import Image
            raw_img_path = tmp_path / "video_thumb.jpg"
            img = Image.new("RGB", (1280, 720), color=(255, 0, 0))
            img.save(raw_img_path, "JPEG")

            MusicDownloader._process_album_cover(tmp_path)

            cover_path = tmp_path / "cover.jpg"
            self.assertTrue(cover_path.is_file())
            with Image.open(cover_path) as c_img:
                self.assertEqual(c_img.size, (720, 720))
            self.assertFalse(raw_img_path.exists())
        finally:
            if tmp_path.exists():
                shutil.rmtree(tmp_path, ignore_errors=True)

    @patch("requests.get")
    def test_process_album_cover_downloads_official_high_res(self, mock_get, tmp_path=Path("./test_cover_dl")):
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            from PIL import Image
            import io
            buf = io.BytesIO()
            Image.new("RGB", (1200, 1200), color=(0, 255, 0)).save(buf, format="JPEG")
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = buf.getvalue()
            mock_get.return_value = mock_resp

            MusicDownloader._process_album_cover(tmp_path, cover_url="https://yt3.googleusercontent.com/test=w120-h120-l90-rj")

            cover_path = tmp_path / "cover.jpg"
            self.assertTrue(cover_path.is_file())
            with Image.open(cover_path) as c_img:
                self.assertEqual(c_img.size, (1200, 1200))
        finally:
            if tmp_path.exists():
                shutil.rmtree(tmp_path, ignore_errors=True)

    @patch("requests.get")
    def test_process_album_cover_itunes_search(self, mock_get, tmp_path=Path("./test_cover_itunes")):
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            from PIL import Image
            import io
            buf = io.BytesIO()
            Image.new("RGB", (1200, 1200), color=(10, 20, 30)).save(buf, format="JPEG")

            mock_search_resp = MagicMock()
            mock_search_resp.status_code = 200
            mock_search_resp.json.return_value = {
                "results": [{"artworkUrl100": "https://example.com/art/100x100bb.jpg"}]
            }

            mock_art_resp = MagicMock()
            mock_art_resp.status_code = 200
            mock_art_resp.content = buf.getvalue()

            def side_effect_get(url, **kwargs):
                if "itunes.apple.com/search" in url:
                    return mock_search_resp
                return mock_art_resp

            mock_get.side_effect = side_effect_get

            MusicDownloader._process_album_cover(
                tmp_path,
                album_name="SOUR",
                album_artist="Olivia Rodrigo",
            )

            cover_path = tmp_path / "cover.jpg"
            self.assertTrue(cover_path.is_file())
            with Image.open(cover_path) as c_img:
                self.assertEqual(c_img.size, (1200, 1200))
        finally:
            if tmp_path.exists():
                shutil.rmtree(tmp_path, ignore_errors=True)

    @patch("mutagen.mp4.MP4")
    def test_process_album_metadata_shields_picard_tags(self, mock_mp4_cls, tmp_path=Path("./test_meta_shield")):
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            m4a = tmp_path / "01 - Ignacio Ormazábal, Alanys Lagos, CRISTIAN SANDOVAL - VOLVERÁS.m4a"
            m4a.write_bytes(b"dummy")

            fake_tags = {
                "©ART": ["Ignacio Ormazábal, Alanys Lagos, CRISTIAN SANDOVAL"],
                "©day": ["20260603"],
            }
            mock_mp4_instance = MagicMock()
            mock_mp4_instance.__getitem__.side_effect = fake_tags.__getitem__
            mock_mp4_instance.__setitem__.side_effect = fake_tags.__setitem__
            mock_mp4_instance.__contains__.side_effect = fake_tags.__contains__
            mock_mp4_instance.get.side_effect = fake_tags.get
            mock_mp4_cls.return_value = mock_mp4_instance

            MusicDownloader._process_album_metadata(
                tmp_path,
                album_artist="Alanys Lagos",
                album_name="VOLVERÁS",
                is_single=True,
            )

            # Assert Album Artist is set cleanly for Picard with native aART
            self.assertEqual(fake_tags.get("aART"), ["Alanys Lagos"])
            self.assertNotIn("----:com.apple.iTunes:ALBUMARTIST", fake_tags)
            self.assertEqual(fake_tags.get("©alb"), ["VOLVERÁS"])
            self.assertEqual(fake_tags.get("trkn"), [(1, 1)])
            self.assertEqual(fake_tags.get("disk"), [(1, 1)])
            # Assert Track Artists are split into multi-value native atom
            self.assertEqual(
                fake_tags.get("©ART"),
                ["Ignacio Ormazábal", "Alanys Lagos", "CRISTIAN SANDOVAL"]
            )
            mock_mp4_instance.save.assert_called_once()
        finally:
            if tmp_path.exists():
                shutil.rmtree(tmp_path, ignore_errors=True)

    def test_split_artists(self):
        self.assertEqual(
            MusicDownloader.split_artists("Arelys Henao, Grupo Exterminador"),
            ["Arelys Henao", "Grupo Exterminador"]
        )
        self.assertEqual(
            MusicDownloader.split_artists("La Ley con Ely Guerra"),
            ["La Ley", "Ely Guerra"]
        )
        self.assertEqual(
            MusicDownloader.split_artists("BURNOUT SYNDROMES feat . Honoo Shiro Retsu"),
            ["BURNOUT SYNDROMES", "Honoo Shiro Retsu"]
        )
        self.assertEqual(
            MusicDownloader.split_artists("Tyler, The Creator"),
            ["Tyler, The Creator"]
        )
        self.assertEqual(
            MusicDownloader.split_artists("Earth, Wind & Fire"),
            ["Earth, Wind & Fire"]
        )
        self.assertEqual(
            MusicDownloader.split_artists("Fear, and Loathing in Las Vegas"),
            ["Fear, and Loathing in Las Vegas"]
        )


class TestUniversalResolver(unittest.TestCase):

    def setUp(self):
        self.mock_yt = MagicMock()
        self.resolver = UniversalResolver(yt=self.mock_yt)

    @patch.object(UniversalResolver, "_resolve_handle_to_channel_id", return_value="UC_daft")
    def test_resolve_input_at_handle(self, mock_resolve_handle):
        self.mock_yt.get_artist.return_value = {
            "name": "Daft Punk",
            "albums": {"results": [{"title": "Discovery", "audioPlaylistId": "OLAK5uy_disc"}]},
            "singles": {"results": []},
        }
        res = self.resolver.resolve_input("@daftpunk")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["kind"], "artist")
        self.assertEqual(res["artist"], "Daft Punk")

    def test_resolve_url_playlist(self):
        self.mock_yt.get_playlist.return_value = {
            "title": "Random Access Memories",
            "author": "Daft Punk",
            "tracks": [
                {"title": "Give Life Back to Music", "artists": [{"name": "Daft Punk"}]},
                {"title": "The Game of Love", "artists": [{"name": "Daft Punk"}]},
            ],
        }
        res = self.resolver.resolve_url("https://music.youtube.com/playlist?list=OLAK5uy_test")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["kind"], "album")
        self.assertEqual(len(res["albums"]), 1)
        self.assertEqual(res["albums"][0].album_name, "Random Access Memories")
        self.assertEqual(res["albums"][0].artist_name, "Daft Punk")
        self.assertEqual(len(res["albums"][0].tracks), 2)

    def test_resolve_url_watch_track(self):
        self.mock_yt.get_song.return_value = {
            "videoDetails": {
                "title": "A Sky Full of Stars",
                "author": "Coldplay",
            }
        }
        res = self.resolver.resolve_url("https://music.youtube.com/watch?v=KWuyx6yZ21U")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["kind"], "track")
        self.assertEqual(len(res["tracks"]), 1)
        self.assertEqual(res["tracks"][0].title, "A Sky Full of Stars")
        self.assertEqual(res["tracks"][0].artist, "Coldplay")

    def test_resolve_url_channel(self):
        self.mock_yt.get_artist.return_value = {
            "name": "Daft Punk",
            "albums": {"results": [{"title": "Discovery", "audioPlaylistId": "OLAK5uy_disc"}]},
            "singles": {"results": []},
        }
        res = self.resolver.resolve_url("https://music.youtube.com/channel/UCRr1xG_2WIDs18a6cIiCxeA")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["kind"], "artist")
        self.assertEqual(res["artist"], "Daft Punk")
        self.assertEqual(len(res["albums"]), 1)

    def test_resolve_artist_discography(self):
        self.mock_yt.search.return_value = [{"browseId": "UC_daft", "artist": "Daft Punk"}]
        self.mock_yt.get_artist.return_value = {
            "name": "Daft Punk",
            "albums": {
                "results": [
                    {"title": "Homework", "audioPlaylistId": "OLAK5uy_hw"},
                    {"title": "Discovery", "audioPlaylistId": "OLAK5uy_disc"},
                ]
            },
            "singles": {
                "results": [
                    {"title": "One More Time", "browseId": "MPREb_single1"},
                ]
            },
        }
        self.mock_yt.get_album.return_value = {
            "title": "One More Time",
            "audioPlaylistId": "OLAK5uy_single_pid",
        }

        res = self.resolver.resolve_artist_discography("Daft Punk")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["kind"], "artist")
        self.assertEqual(res["total_albums"], 2)
        self.assertEqual(res["total_singles"], 1)
        self.assertEqual(len(res["albums"]), 3)
        self.assertFalse(res["albums"][0].is_single)
        self.assertTrue(res["albums"][2].is_single)

    def test_resolve_album(self):
        self.mock_yt.search.return_value = [
            {
                "title": "A Night at the Opera",
                "playlistId": "OLAK5uy_queen_opera",
                "artists": [{"name": "Queen"}],
            }
        ]
        res = self.resolver.resolve_album("A Night at the Opera")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["kind"], "album")
        self.assertEqual(res["title"], "A Night at the Opera")
        self.assertEqual(res["artist"], "Queen")
        self.assertEqual(len(res["albums"]), 1)
        self.assertEqual(res["albums"][0].url, "https://music.youtube.com/playlist?list=OLAK5uy_queen_opera")

    def test_resolve_song(self):
        self.mock_yt.search.return_value = [
            {
                "title": "Bohemian Rhapsody",
                "videoId": "fJ9rUzIMcZQ",
                "artists": [{"name": "Queen"}],
            }
        ]
        res = self.resolver.resolve_song("Bohemian Rhapsody")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["kind"], "track")
        self.assertEqual(res["title"], "Bohemian Rhapsody")
        self.assertEqual(len(res["tracks"]), 1)
        self.assertEqual(res["tracks"][0].url, "https://music.youtube.com/watch?v=fJ9rUzIMcZQ")

    def test_resolve_input_prefixes(self):
        with patch.object(self.resolver, "resolve_artist_discography") as mock_disc, \
             patch.object(self.resolver, "resolve_album") as mock_alb, \
             patch.object(self.resolver, "resolve_song") as mock_song, \
             patch.object(self.resolver, "resolve_url") as mock_url:

            self.resolver.resolve_input("artista Daft Punk")
            mock_disc.assert_called_once_with("Daft Punk")

            self.resolver.resolve_input("album Random Access Memories")
            mock_alb.assert_called_once_with("Random Access Memories")

            self.resolver.resolve_input("cancion Bohemian Rhapsody")
            mock_song.assert_called_once_with("Bohemian Rhapsody")

            self.resolver.resolve_input("https://music.youtube.com/playlist?list=OLAK5uy_xxx")
            mock_url.assert_called_once_with("https://music.youtube.com/playlist?list=OLAK5uy_xxx")

    def test_resolve_input_heuristics(self):
        # Case 1: Exact match for artist
        def mock_search(query, filter, limit=1):
            if filter == "artists":
                return [{"artist": "Queen", "browseId": "UC_queen"}]
            if filter == "albums":
                return [{"title": "Greatest Hits", "playlistId": "OLAK5uy_gh"}]
            return []

        self.mock_yt.search.side_effect = mock_search
        with patch.object(self.resolver, "resolve_artist_discography") as mock_disc, \
             patch.object(self.resolver, "resolve_album") as mock_alb:

            self.resolver.resolve_input("Queen")
            mock_disc.assert_called_once_with("Queen")
            mock_alb.assert_not_called()

        # Case 2: Exact match for album
        def mock_search_album(query, filter, limit=1):
            if filter == "artists":
                return [{"artist": "Daft Punk", "browseId": "UC_daft"}]
            if filter == "albums":
                return [{"title": "Random Access Memories", "playlistId": "OLAK5uy_ram"}]
            return []

        self.mock_yt.search.side_effect = mock_search_album
        with patch.object(self.resolver, "resolve_artist_discography") as mock_disc, \
             patch.object(self.resolver, "resolve_album") as mock_alb:

            self.resolver.resolve_input("Random Access Memories")
            mock_alb.assert_called_once_with("Random Access Memories")
            mock_disc.assert_not_called()


class TestDownloadQueue(unittest.TestCase):

    def test_queue_process_job_and_skips(self, tmp_path=Path("./test_queue_dir")):
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            mock_downloader = MagicMock()
            mock_downloader.download_dir = tmp_path
            mock_downloader.cookies_list = []
            mock_downloader.sanitize_name.side_effect = lambda s: s.replace(" ", "_")
            mock_downloader.download_album.return_value = (True, "OK")

            mock_navidrome = MagicMock()
            # Album 1 already in Navidrome
            mock_navidrome.find_album_match.side_effect = lambda alb, art: {"id": "alb_1"} if "Existente" in alb else None

            mock_listener = MagicMock()

            queue_manager = DownloadQueue(
                downloader=mock_downloader,
                navidrome=mock_navidrome,
                listener=mock_listener,
                win_dest="M:\\music\\downloads",
            )

            alb_existing = ResolvedAlbum(
                url="https://music.youtube.com/playlist?list=OLAK5uy_1",
                album_name="Album Existente",
                artist_name="Artista A",
            )
            alb_new = ResolvedAlbum(
                url="https://music.youtube.com/playlist?list=OLAK5uy_2",
                album_name="Album Nuevo",
                artist_name="Artista B",
            )

            job = DownloadJob(
                job_id="test_job_1",
                chat_id="12345",
                thread_id=None,
                title="Test Job",
                albums=[alb_existing, alb_new],
                tracks=[],
            )

            pos = queue_manager.enqueue(job)
            self.assertEqual(pos, 0)

            # Wait for job to be processed by worker
            queue_manager.queue.join()

            # mock_downloader.download_album should only be called for alb_new (alb_existing skipped)
            mock_downloader.download_album.assert_called_once_with(alb_new)
            # Listener should have sent start, skip, download, and summary messages
            self.assertGreaterEqual(mock_listener.send_reply.call_count, 4)

            # Check status when idle
            self.assertEqual(queue_manager.get_status(), "Inactivo")
        finally:
            if tmp_path.exists():
                shutil.rmtree(tmp_path, ignore_errors=True)

    def test_queue_process_job_partial_album_downloads_missing_tracks(self, tmp_path=Path("./test_q_partial")):
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)
        tmp_path.mkdir(parents=True, exist_ok=True)

        try:
            mock_downloader = MagicMock()
            mock_downloader.download_dir = tmp_path
            mock_downloader.sanitize_name = lambda s: s.replace(" ", "_")
            mock_downloader.cookies_list = []
            mock_downloader.download_album_tracks.return_value = (True, str(tmp_path / "Discovery"))

            # YouTube Music has 3 tracks: One More Time (1), Aerodynamic (2), Digital Love (3)
            mock_downloader.get_album_tracklist.return_value = [
                {"index": 1, "title": "One More Time", "id": "v1"},
                {"index": 2, "title": "Aerodynamic", "id": "v2"},
                {"index": 3, "title": "Digital Love", "id": "v3"},
            ]

            mock_navidrome = MagicMock()
            # Navidrome finds the album
            mock_navidrome.find_album_match.return_value = {"id": "alb_discovery", "name": "Discovery"}
            # But Navidrome only has track 1 ("One More Time")
            mock_navidrome.get_album_tracks.return_value = [
                {"title": "One More Time", "track": 1}
            ]

            mock_listener = MagicMock()

            queue_manager = DownloadQueue(
                downloader=mock_downloader,
                navidrome=mock_navidrome,
                listener=mock_listener,
                win_dest="M:\\music\\downloads",
            )

            album = ResolvedAlbum(
                url="https://music.youtube.com/playlist?list=OLAK5uy_discovery",
                album_name="Discovery",
                artist_name="Daft Punk",
            )

            job = DownloadJob(
                job_id="test_partial_job",
                chat_id="12345",
                thread_id=None,
                title="Test Partial Album",
                albums=[album],
                tracks=[],
            )

            queue_manager.enqueue(job)
            queue_manager.queue.join()

            # Must NOT call download_album (which downloads entire album)
            mock_downloader.download_album.assert_not_called()
            # MUST call download_album_tracks with missing indices [2, 3]!
            mock_downloader.download_album_tracks.assert_called_once_with(album, [2, 3])

            # Messages sent must include partial notification
            all_replies = [call[0][1] for call in mock_listener.send_reply.call_args_list]
            self.assertTrue(any("parcial en biblioteca" in r.lower() for r in all_replies))
            self.assertTrue(any("pistas faltantes descargadas" in r.lower() for r in all_replies))
        finally:
            if tmp_path.exists():
                shutil.rmtree(tmp_path, ignore_errors=True)

    def test_queue_process_job_single_tracks_navidrome_and_disk_duplicates(self, tmp_path=Path("./test_q_singles")):
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)
        tmp_path.mkdir(parents=True, exist_ok=True)

        try:
            mock_downloader = MagicMock()
            mock_downloader.download_dir = tmp_path
            mock_downloader.sanitize_name = lambda s: s.replace(" ", "_")
            mock_downloader.cookies_list = []
            mock_downloader.download_track.return_value = (True, str(tmp_path / "_Singles" / "new.m4a"))

            # Track 1 already in Navidrome
            mock_navidrome = MagicMock()
            mock_navidrome.find_best_match.side_effect = lambda title, artist: (
                {"id": "s1", "title": title, "album": "Random Access Memories"} if "Get Lucky" in title else None
            )

            # Track 2 already on disk in _Singles
            singles_dir = tmp_path / "_Singles"
            singles_dir.mkdir(parents=True, exist_ok=True)
            (singles_dir / "daft_punk_-_one_more_time.m4a").write_bytes(b"0" * 1024)

            mock_listener = MagicMock()

            queue_manager = DownloadQueue(
                downloader=mock_downloader,
                navidrome=mock_navidrome,
                listener=mock_listener,
                win_dest="M:\\music\\downloads",
            )

            trk_nav = ResolvedTrack(url="https://music.youtube.com/watch?v=1", title="Get Lucky", artist="Daft Punk")
            trk_disk = ResolvedTrack(url="https://music.youtube.com/watch?v=2", title="One More Time", artist="Daft Punk")
            trk_new = ResolvedTrack(url="https://music.youtube.com/watch?v=3", title="Instant Crush", artist="Daft Punk")

            job = DownloadJob(
                job_id="test_singles_job",
                chat_id="12345",
                thread_id=None,
                title="Test Singles Job",
                albums=[],
                tracks=[trk_nav, trk_disk, trk_new],
            )

            queue_manager.enqueue(job)
            queue_manager.queue.join()

            # download_track must ONLY be called for trk_new (others skipped)
            mock_downloader.download_track.assert_called_once_with(trk_new)

            all_replies = [call[0][1] for call in mock_listener.send_reply.call_args_list]
            self.assertTrue(any("ya en tu biblioteca" in r.lower() for r in all_replies))
            self.assertTrue(any("ya en disco" in r.lower() for r in all_replies))
            self.assertTrue(any("cancion descargada" in r.lower().replace("ó", "o") for r in all_replies))
        finally:
            if tmp_path.exists():
                shutil.rmtree(tmp_path, ignore_errors=True)

    def test_download_queue_duration_and_html_cleaning(self):
        self.assertEqual(DownloadQueue._format_duration(0), "")
        self.assertEqual(DownloadQueue._format_duration(45), "0:45")
        self.assertEqual(DownloadQueue._format_duration(200), "3:20")
        self.assertEqual(DownloadQueue._format_duration(3665), "1:01:05")

    def test_queue_circuit_breaker_stops_after_3_youtube_blocks(self, tmp_path=Path("./test_q_cb")):
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            mock_downloader = MagicMock()
            mock_downloader.download_dir = tmp_path
            mock_downloader.library_dir = tmp_path
            mock_downloader.cookies_list = []
            mock_downloader.sanitize_name.side_effect = lambda s: s
            mock_downloader.verify_album_integrity.return_value = (False, 0, "empty")
            # Always fail with YouTube bot block
            mock_downloader.download_album.return_value = (False, "Posible bloqueo temporal de YouTube: Sign in to confirm you're not a bot")

            mock_listener = MagicMock()
            queue_manager = DownloadQueue(
                downloader=mock_downloader,
                navidrome=None,
                listener=mock_listener,
                win_dest="M:\\music\\downloads",
            )

            albums = [
                ResolvedAlbum(url=f"https://music.youtube.com/{i}", album_name=f"Album {i}", artist_name="Artist")
                for i in range(1, 10)
            ]
            job = DownloadJob(
                job_id="test_cb_job",
                chat_id="12345",
                thread_id=None,
                title="Test CB Job",
                albums=albums,
                tracks=[],
            )

            queue_manager.enqueue(job)
            queue_manager.queue.join()

            # Should have stopped after 3 attempts, NOT 9!
            self.assertEqual(mock_downloader.download_album.call_count, 3)

            all_replies = [call[0][1] for call in mock_listener.send_reply.call_args_list]
            self.assertTrue(any("Circuit Breaker" in r for r in all_replies))
        finally:
            if tmp_path.exists():
                shutil.rmtree(tmp_path, ignore_errors=True)

    def test_queue_cancel_active_aborts_jobs(self, tmp_path=Path("./test_q_cancel")):
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            mock_downloader = MagicMock()
            mock_downloader.download_dir = tmp_path
            mock_downloader.library_dir = tmp_path
            mock_downloader.cookies_list = []
            mock_downloader.sanitize_name.side_effect = lambda s: s
            mock_downloader.verify_album_integrity.return_value = (False, 0, "empty")

            mock_listener = MagicMock()
            queue_manager = DownloadQueue(
                downloader=mock_downloader,
                navidrome=None,
                listener=mock_listener,
                win_dest="M:\\music\\downloads",
            )

            success, msg = queue_manager.cancel_active()
            self.assertFalse(success)
            self.assertIn("No hay descargas activas", msg)
        finally:
            if tmp_path.exists():
                shutil.rmtree(tmp_path, ignore_errors=True)

    def test_download_queue_state_persistence(self, tmp_path=Path("./test_q_persist")):
        tmp_path.mkdir(parents=True, exist_ok=True)
        queue_file = tmp_path / "pending_queue.json"
        try:
            mock_downloader = MagicMock()
            mock_downloader.download_dir = tmp_path
            mock_downloader.cookies_list = []

            alb = ResolvedAlbum(
                url="https://music.youtube.com/playlist?list=OLAK5uy_persist",
                album_name="Persistent Album",
                artist_name="Persistent Artist",
            )
            job = DownloadJob(
                job_id="persist_job_1",
                chat_id="9999",
                thread_id="10",
                title="Persistent Job",
                albums=[alb],
                tracks=[],
                source_desc="/add persist",
            )

            with patch.object(DownloadQueue, "_worker_loop"):
                # 1. Enqueue job into q1 and verify written to file
                q1 = DownloadQueue(
                    downloader=mock_downloader,
                    navidrome=None,
                    listener=None,
                    win_dest="M:\\music",
                    queue_file=str(queue_file),
                )
                q1.enqueue(job)
                self.assertTrue(queue_file.is_file())

                # 2. Simulate process shutdown and new instance startup
                q2 = DownloadQueue(
                    downloader=mock_downloader,
                    navidrome=None,
                    listener=None,
                    win_dest="M:\\music",
                    queue_file=str(queue_file),
                )

                self.assertEqual(q2.queue.qsize(), 1)
                restored_job = q2.queue.get_nowait()
                self.assertEqual(restored_job.job_id, "persist_job_1")
                self.assertEqual(restored_job.title, "Persistent Job")
                self.assertEqual(len(restored_job.albums), 1)
                self.assertEqual(restored_job.albums[0].album_name, "Persistent Album")
        finally:
            if tmp_path.exists():
                shutil.rmtree(tmp_path, ignore_errors=True)

        raw_html = "<h3>Title</h3>\n<h4>Subtitle</h4>\n<table bordered striped>\n  <tr><td>A</td></tr>\n</table>"
        cleaned = DownloadQueue._clean_rich_html(raw_html)
        self.assertNotIn("\n", cleaned)
        self.assertIn("<h3>Title</h3><h4>Subtitle</h4>", cleaned)

    def test_download_queue_build_album_rich_html_structure(self):
        queue_manager = DownloadQueue(
            downloader=MagicMock(),
            navidrome=MagicMock(),
            listener=MagicMock(),
            win_dest="M:\\music\\downloads",
            bot_token="test_token",
        )
        songs = [
            {"disc": 1, "track": 1, "title": "Track One", "duration": 210},
            {"disc": 1, "track": 2, "title": "Track Two", "duration": 185},
        ]
        html_out = queue_manager._build_album_rich_html(
            album_name="Test Album",
            artist_name="Test Artist",
            year="2024",
            genre="Rock",
            disc_count=1,
            song_count=2,
            total_duration_sec=395,
            songs=songs,
            nav_url="https://navidrome.example.com/#/album/abc",
        )

        self.assertIn("<img src=\"tg://photo?id=cover\" />", html_out)
        self.assertIn("<h3>📚 Test Album</h3>", html_out)
        self.assertIn("<h4>👤 Test Artist</h4>", html_out)
        self.assertIn("<table bordered striped>", html_out)
        self.assertIn("📅 Año", html_out)
        self.assertIn("2024", html_out)
        self.assertIn("📦 Género", html_out)
        self.assertIn("Rock", html_out)
        self.assertIn("💿 Discos", html_out)
        self.assertIn("🔢 Canciones", html_out)
        self.assertIn("🕒 Duración", html_out)
        self.assertIn("6:35", html_out)
        self.assertIn("<details><summary>🎵 Ver Lista de Canciones</summary>", html_out)
        self.assertIn("<b>01.</b> Track One (3:30)", html_out)
        self.assertIn("<b>02.</b> Track Two (3:05)", html_out)
        self.assertIn("🌐 Escuchar en Navidrome", html_out)
        self.assertIn("https://navidrome.example.com/#/album/abc", html_out)

    @patch("requests.post")
    def test_download_queue_send_album_rich_notification(self, mock_post, tmp_path=Path("./test_q_rich")):
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)
        tmp_path.mkdir(parents=True, exist_ok=True)

        try:
            # Create a mock cover.jpg
            (tmp_path / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0test_jpeg_bytes")

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"ok": True, "result": {"message_id": 999}}
            mock_post.return_value = mock_resp

            mock_nav = MagicMock()
            mock_nav.base_url = "https://navidrome.example.com"
            mock_nav.find_album_match.return_value = {"id": "alb_123"}

            queue_manager = DownloadQueue(
                downloader=MagicMock(),
                navidrome=mock_nav,
                listener=MagicMock(),
                win_dest="M:\\music\\downloads",
                bot_token="test_token_xyz",
            )

            album = ResolvedAlbum(
                url="https://music.youtube.com/playlist?list=OLAK5uy_123",
                album_name="Album X",
                artist_name="Artist Y",
                tracks=["Song A", "Song B"],
            )

            ok = queue_manager._send_album_rich_notification(
                chat_id="12345",
                thread_id="42",
                album=album,
                target_folder=tmp_path,
            )
            self.assertTrue(ok)
            mock_post.assert_called_once()
            called_url = mock_post.call_args[0][0]
            self.assertEqual(called_url, "https://api.telegram.org/bottest_token_xyz/sendRichMessage")
            called_data = mock_post.call_args[1]["data"]
            self.assertEqual(called_data["chat_id"], "12345")
            self.assertEqual(called_data["message_thread_id"], "42")
            rich_parsed = json.loads(called_data["rich_message"])
            self.assertIn("📚 Album X", rich_parsed["html"])
            self.assertIn("👤 Artist Y", rich_parsed["html"])
            self.assertIn("https://navidrome.example.com/#/album/alb_123", rich_parsed["html"])
            self.assertEqual(rich_parsed["media"][0]["id"], "cover")
            called_files = mock_post.call_args[1]["files"]
            self.assertIn("cover", called_files)
        finally:
            if tmp_path.exists():
                shutil.rmtree(tmp_path, ignore_errors=True)

    @patch.object(DownloadQueue, "_send_album_rich_notification")
    def test_process_job_calls_rich_notification_only_on_full_album_download(self, mock_rich_notify, tmp_path=Path("./test_q_full_dl")):
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)
        tmp_path.mkdir(parents=True, exist_ok=True)

        try:
            mock_downloader = MagicMock()
            mock_downloader.download_dir = tmp_path
            mock_downloader.sanitize_name = lambda s: s.replace(" ", "_")
            mock_downloader.cookies_list = []
            mock_downloader.verify_album_integrity.return_value = (False, 0, "No existe")
            mock_downloader.download_album.return_value = (True, str(tmp_path / "Full_Album"))

            mock_listener = MagicMock()
            queue_manager = DownloadQueue(
                downloader=mock_downloader,
                navidrome=None,
                listener=mock_listener,
                win_dest="M:\\music\\downloads",
                bot_token="test_token",
            )

            album = ResolvedAlbum(
                url="https://music.youtube.com/playlist?list=OLAK5uy_999",
                album_name="Full Album",
                artist_name="Artist Z",
                tracks=["Track 1", "Track 2"],
            )

            job = DownloadJob(
                job_id="test_full_album_job",
                chat_id="12345",
                thread_id="100",
                title="Test Full Album",
                albums=[album],
                tracks=[],
            )

            queue_manager.enqueue(job)
            queue_manager.queue.join()

            # download_album was called
            mock_downloader.download_album.assert_called_once_with(album)

            # _send_album_rich_notification MUST have been called instead of old text reply!
            mock_rich_notify.assert_called_once()
            call_args = mock_rich_notify.call_args[0]
            self.assertEqual(call_args[0], "12345")  # chat_id
            self.assertEqual(call_args[1], "100")    # thread_id
            self.assertEqual(call_args[2], album)    # album
            self.assertEqual(call_args[3], tmp_path / "Full_Album")  # target_folder

            # Old message "[idx/total] Álbum descargado" should NOT have been sent
            all_replies = [call[0][1] for call in mock_listener.send_reply.call_args_list]
            self.assertFalse(any("álbum descargado:" in r.lower() for r in all_replies))
        finally:
            if tmp_path.exists():
                shutil.rmtree(tmp_path, ignore_errors=True)



class TestStateTrackerDiscovery(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path("test_tmp_state_tracker_disc")
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.tmp_dir / ".state.json"
        self.tracker = StateTracker(self.state_file)

    def tearDown(self):
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_save_and_get_last_discovered(self):
        self.assertEqual(self.tracker.get_last_discovered(), [])
        sample_items = [
            {"kind": "album", "title": "Album 1", "artist": "Artist 1", "url": "https://music.youtube.com/playlist?list=OL1"},
            {"kind": "track", "title": "Track 2", "artist": "Artist 2", "url": "https://music.youtube.com/watch?v=V2"},
        ]
        self.tracker.save_last_discovered(sample_items)
        retrieved = self.tracker.get_last_discovered()
        self.assertEqual(len(retrieved), 2)
        self.assertEqual(retrieved[0]["title"], "Album 1")
        self.assertEqual(retrieved[1]["title"], "Track 2")


class TestYouTubeMusicDiscoverer(unittest.TestCase):
    @patch("notifier.YTMusic")
    def test_build_browser_headers(self, mock_ytmusic):
        tmp_cookie = Path("test_tmp_cookie.txt")
        try:
            # Write a mock netscape cookie with SAPISID
            tmp_cookie.write_text(
                "# Netscape HTTP Cookie File\n"
                ".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSAPISID\tmock_sapisid_value\n"
                ".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tmock_sid_value\n",
                encoding="utf-8"
            )
            headers = YouTubeMusicDiscoverer._build_browser_headers(tmp_cookie)
            self.assertIsNotNone(headers)
            self.assertIn("Authorization", headers)
            self.assertIn("SAPISIDHASH", headers["Authorization"])
            self.assertEqual(headers["x-goog-authuser"], "0")
        finally:
            tmp_cookie.unlink(missing_ok=True)

    @patch("notifier.YTMusic")
    def test_get_recommendations(self, mock_yt_cls):
        mock_yt = MagicMock()
        mock_yt_cls.return_value = mock_yt

        mock_yt.get_home.return_value = [
            {
                "title": "Albums for you",
                "contents": [
                    {
                        "title": "Unreleased Hits",
                        "artists": [{"name": "Pop Artist"}],
                        "browseId": "MPREb_12345",
                        "thumbnails": [{"url": "http://example.com/thumb.jpg"}],
                    }
                ]
            }
        ]
        mock_yt.get_album.return_value = {
            "audioPlaylistId": "OLAK5uy_custom_playlist",
            "year": "2024",
            "trackCount": 2,
            "tracks": [{"title": "Track One"}, {"title": "Track Two"}],
        }

        discoverer = YouTubeMusicDiscoverer(cookie_file=None, music_dir=None)
        discoverer.yt = mock_yt

        mock_nav = MagicMock()
        mock_nav.find_album_match.return_value = None  # Not in library

        recs = discoverer.get_recommendations(navidrome=mock_nav, limit=5)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["title"], "Unreleased Hits")
        self.assertEqual(recs[0]["artist"], "Pop Artist")
        self.assertEqual(recs[0]["url"], "https://music.youtube.com/playlist?list=OLAK5uy_custom_playlist")
        self.assertEqual(recs[0]["year"], "2024")
        self.assertEqual(recs[0]["track_count"], 2)

    @patch("notifier.YTMusic")
    def test_get_recommendations_filters_existing(self, mock_yt_cls):
        mock_yt = MagicMock()
        mock_yt_cls.return_value = mock_yt

        mock_yt.get_home.return_value = [
            {
                "title": "Albums for you",
                "contents": [
                    {
                        "title": "Already Owned Album",
                        "artists": [{"name": "Known Artist"}],
                        "browseId": "MPREb_99999",
                    }
                ]
            }
        ]
        discoverer = YouTubeMusicDiscoverer(cookie_file=None, music_dir=None)
        discoverer.yt = mock_yt

        mock_nav = MagicMock()
        mock_nav.find_album_match.return_value = {"id": "alb-1", "title": "Already Owned Album"}

        recs = discoverer.get_recommendations(navidrome=mock_nav, limit=5)
        self.assertEqual(len(recs), 0)  # Filtered out!


class TestTelegramBotListenerDiscovery(unittest.TestCase):
    def setUp(self):
        self.mock_sync = MagicMock()
        self.mock_status = MagicMock(return_value="Status OK")
        self.mock_discover = MagicMock()
        self.mock_new_releases = MagicMock()
        self.mock_dl_rec = MagicMock()
        self.mock_dl_nov = MagicMock()

        self.listener = TelegramBotListener(
            bot_token="test_token",
            authorized_chat_ids=["123"],
            on_sync_command=self.mock_sync,
            on_status_command=self.mock_status,
            on_discover_command=self.mock_discover,
            on_new_releases_command=self.mock_new_releases,
            on_download_rec_command=self.mock_dl_rec,
            on_download_nov_command=self.mock_dl_nov,
        )

    def test_dispatch_descubrir(self):
        update = {
            "message": {
                "chat": {"id": 123},
                "text": "/descubrir 5",
            }
        }
        with patch("threading.Thread") as mock_thread:
            self.listener.handle_update(update)
            mock_thread.assert_called_once()
            call_kwargs = mock_thread.call_args[1]
            self.assertEqual(call_kwargs["target"], self.mock_discover)
            self.assertEqual(call_kwargs["args"], ("123", None, "5"))

    def test_dispatch_novedades(self):
        update = {
            "message": {
                "chat": {"id": 123},
                "text": "/novedades",
            }
        }
        with patch("threading.Thread") as mock_thread:
            self.listener.handle_update(update)
            mock_thread.assert_called_once()
            call_kwargs = mock_thread.call_args[1]
            self.assertEqual(call_kwargs["target"], self.mock_new_releases)
            self.assertEqual(call_kwargs["args"], ("123", None, None))

    def test_dispatch_dl_rec(self):
        update = {
            "message": {
                "chat": {"id": 123},
                "text": "/dl_rec 1",
            }
        }
        self.listener.handle_update(update)
        self.mock_dl_rec.assert_called_once_with("123", None, "1")

    def test_dispatch_dl_nov(self):
        update = {
            "message": {
                "chat": {"id": 123},
                "text": "/dl_nov 2",
            }
        }
        self.listener.handle_update(update)
        self.mock_dl_nov.assert_called_once_with("123", None, "2")

    def test_dispatch_cancel(self):
        mock_worker = MagicMock()
        mock_worker.cancel_active.return_value = (True, "Test Job")
        self.listener.download_worker = mock_worker
        update = {
            "message": {
                "chat": {"id": 123},
                "text": "/cancel",
            }
        }
        with patch.object(self.listener, "send_reply") as mock_send:
            self.listener.handle_update(update)
            mock_worker.cancel_active.assert_called_once()
            mock_send.assert_called_once()
            self.assertIn("Cancelando descarga", mock_send.call_args[0][1])

    def test_command_pause_and_resume(self):
        mock_worker = MagicMock()
        mock_worker.pause.return_value = (True, "Cola pausada")
        mock_worker.resume.return_value = (True, "Cola reanudada")
        self.listener.download_worker = mock_worker

        with patch.object(self.listener, "send_reply") as mock_send:
            self.listener.handle_update({
                "update_id": 999,
                "message": {"chat": {"id": 123}, "text": "/pause"}
            })
            mock_worker.pause.assert_called_once()
            mock_send.assert_called_once()
            self.assertIn("Cola pausada", mock_send.call_args[0][1])

        with patch.object(self.listener, "send_reply") as mock_send:
            self.listener.handle_update({
                "update_id": 1000,
                "message": {"chat": {"id": 123}, "text": "/resume"}
            })
            mock_worker.resume.assert_called_once()
            mock_send.assert_called_once()
            self.assertIn("Cola reanudada", mock_send.call_args[0][1])

    @patch.object(TelegramBotListener, "answer_callback_query")
    def test_callback_query_dl_rec(self, mock_answer):
        update = {
            "callback_query": {
                "id": "cq_101",
                "message": {
                    "chat": {"id": 123},
                    "message_thread_id": 456,
                },
                "data": "dl_rec:3",
            }
        }
        self.listener.handle_update(update)
        mock_answer.assert_called_once_with("cq_101", text="⏳ Descargando sugerencia #3...")
        self.mock_dl_rec.assert_called_once_with("123", "456", "3")

    @patch.object(TelegramBotListener, "answer_callback_query")
    def test_callback_query_dl_nov(self, mock_answer):
        update = {
            "callback_query": {
                "id": "cq_102",
                "message": {
                    "chat": {"id": 123},
                },
                "data": "dl_nov:all",
            }
        }
        self.listener.handle_update(update)
        mock_answer.assert_called_once_with("cq_102", text="⏳ Descargando novedad #all...")
        self.mock_dl_nov.assert_called_once_with("123", None, "all")


class TestStateTrackerDiscovery(unittest.TestCase):
    def test_save_and_get_discovered_and_new_releases(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            path = tf.name
        try:
            tracker = StateTracker(path)
            tracker.save_last_discovered([{"title": "Rec 1"}])
            tracker.save_last_new_releases([{"title": "Nov 1"}])

            self.assertEqual(tracker.get_last_discovered(), [{"title": "Rec 1"}])
            self.assertEqual(tracker.get_last_new_releases(), [{"title": "Nov 1"}])

            # Reload from disk
            tracker2 = StateTracker(path)
            self.assertEqual(tracker2.get_last_discovered(), [{"title": "Rec 1"}])
            self.assertEqual(tracker2.get_last_new_releases(), [{"title": "Nov 1"}])
        finally:
            Path(path).unlink(missing_ok=True)


class TestPicardEngine(unittest.TestCase):
    def test_normalize_hyphens(self):
        from modules.picard_engine import PicardEngine
        raw = "Ado\u2010Show\u2013Remix\u2014Edit"
        self.assertEqual(PicardEngine.normalize_hyphens(raw), "Ado-Show-Remix-Edit")
        self.assertEqual(PicardEngine.normalize_hyphens(""), "")

    def test_contains_japanese(self):
        from modules.picard_engine import PicardEngine
        self.assertTrue(PicardEngine.contains_japanese("うっせぇわ"))
        self.assertTrue(PicardEngine.contains_japanese("カタカナ"))
        self.assertTrue(PicardEngine.contains_japanese("漢字"))
        self.assertFalse(PicardEngine.contains_japanese("Hello World!"))
        self.assertFalse(PicardEngine.contains_japanese("12345"))

    def test_romanize_japanese(self):
        from modules.picard_engine import PicardEngine
        # In auto mode (len <= 65), produces dual format
        loan = PicardEngine.romanize_japanese("オリジナル・サウンドトラック")
        self.assertEqual(loan, "オリジナル・サウンドトラック - Original Soundtrack")

        # In romaji mode, produces romaji only
        loan_rom = PicardEngine.romanize_japanese("オリジナル・サウンドトラック", mode="romaji")
        self.assertEqual(loan_rom, "Original Soundtrack")

        # Romaji transliteration
        rom = PicardEngine.romanize_japanese("うっせぇわ")
        self.assertEqual(rom, "うっせぇわ - Usseewa")

        # Dual length limit fallback (> 65 chars -> romaji only)
        long_jp = "これはとても長くて素晴らしいタイトルの曲でありテスト用です"
        long_rom = PicardEngine.romanize_japanese(long_jp, max_dual_len=40)
        self.assertNotIn(" - ", long_rom)  # Falls back to romaji only

        # Already translated title preservation
        already_dual = "プラネタリウム - Planetarium"
        self.assertEqual(PicardEngine.romanize_japanese(already_dual), already_dual)

        # Mixed text
        kanji_rom = PicardEngine.romanize_japanese("進撃の巨人")
        self.assertIn("Shingeki", kanji_rom)

    def test_genre_mapper(self):
        from modules.picard_engine import PicardEngine
        raw_genres = ["anison", "j-rock", "pop"]
        mapped = PicardEngine.apply_genre_mapper(raw_genres, apply_first_only=True)
        self.assertEqual(mapped, ["Anime"])

        all_mapped = PicardEngine.apply_genre_mapper(["jpop", "video game music"], apply_first_only=False)
        self.assertEqual(all_mapped, ["J-Pop", "Game Soundtrack"])

    def test_enhance_title(self):
        from modules.picard_engine import PicardEngine
        self.assertEqual(PicardEngine.enhance_title("one more time"), "One More Time")
        self.assertEqual(PicardEngine.enhance_title("DJ got us fallin' in love"), "DJ Got Us Fallin' in Love")
        self.assertEqual(PicardEngine.enhance_title("soundtrack for the movie"), "Soundtrack for the Movie")
        self.assertEqual(PicardEngine.make_sort_name("The Beatles"), "Beatles, The")

    def test_apply_release_type(self):
        from modules.picard_engine import PicardEngine
        self.assertEqual(PicardEngine.apply_release_type("Stay Gold", is_single=True), "Stay Gold (single)")
        self.assertEqual(PicardEngine.apply_release_type("Stay Gold (single)", is_single=True), "Stay Gold (single)")
        self.assertEqual(PicardEngine.apply_release_type("Short Album", is_single=False, is_ep=True), "Short Album EP")
        self.assertEqual(PicardEngine.apply_release_type("Short Album EP", is_single=False, is_ep=True), "Short Album EP")

    def test_is_soundtrack(self):
        from modules.picard_engine import PicardEngine
        self.assertTrue(PicardEngine.is_soundtrack("Interstellar (Original Motion Picture Soundtrack)"))
        self.assertTrue(PicardEngine.is_soundtrack("Anime OST", genre="Soundtrack"))
        self.assertTrue(PicardEngine.is_soundtrack("Game BGM Collection"))
        self.assertFalse(PicardEngine.is_soundtrack("Random Access Memories"))

    def test_evaluate_naming_script_cases(self):
        from modules.picard_engine import PicardEngine
        # 1. J-Music by Japanese characters
        folder, filename = PicardEngine.evaluate_naming_script(
            artist="Ado",
            album="Kyogen",
            title="うっせぇわ",
            date="2022-01-26",
            tracknumber=1,
        )
        self.assertEqual(folder, "J-Music/Ado/[2022] - Kyogen")
        self.assertEqual(filename, "01 - うっせぇわ.m4a")

        # 2. General Pop (Western)
        folder, filename = PicardEngine.evaluate_naming_script(
            artist="Enrique Iglesias",
            album="Sex and Love",
            title="Bailando",
            date="2014-03-18",
            tracknumber=6,
        )
        self.assertEqual(folder, "General/Enrique Iglesias/[2014] - Sex and Love")
        self.assertEqual(filename, "06 - Bailando.m4a")

        # 3. Soundtrack Worldwide
        folder, filename = PicardEngine.evaluate_naming_script(
            artist="Hans Zimmer",
            album="Interstellar (Soundtrack)",
            title="Cornfield Chase",
            albumartist="Various Artists",
            date="2014-11-17",
            tracknumber=1,
        )
        self.assertEqual(folder, "Soundtracks/Worldwide/[2014] - Interstellar (Soundtrack)")
        self.assertEqual(filename, "01 - Cornfield Chase.m4a")

        # 4. Soundtrack J-Music
        folder, filename = PicardEngine.evaluate_naming_script(
            artist="Various Artists",
            album="Demon Slayer OST",
            title="Gurenge",
            albumartist="Various Artists",
            date="2020",
            genre="Anime",
            tracknumber=1,
        )
        self.assertEqual(folder, "Soundtracks/J-Music/[2020] - Demon Slayer OST")
        self.assertEqual(filename, "01 - Gurenge.m4a")

        # 5. Compilation
        folder, filename = PicardEngine.evaluate_naming_script(
            artist="Various Artists",
            album="Top Hits 2021",
            title="Track 1",
            albumartist="Various Artists",
            date="2021",
            tracknumber=1,
        )
        self.assertEqual(folder, "Compilaciones/[2021] - Top Hits 2021")
        self.assertEqual(filename, "01 - Track 1.m4a")

        # 6. Multi-disc folder
        folder, filename = PicardEngine.evaluate_naming_script(
            artist="Daft Punk",
            album="Discovery",
            title="Aerodynamic",
            date="2001",
            totaldiscs=2,
            discnumber=2,
            tracknumber=2,
        )
        self.assertEqual(folder, "General/Daft Punk/[2001] - Discovery/Disco 2")
        self.assertEqual(filename, "02 - Aerodynamic.m4a")

    def test_deduplicator(self):
        import tempfile
        from modules.picard_engine import PicardEngine
        with tempfile.TemporaryDirectory() as tmpdir:
            lib_root = Path(tmpdir)
            target_file = lib_root / "General" / "Artist" / "Album" / "01 - Song.m4a"
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text("old version")

            # Run deduplicator
            ok = PicardEngine.handle_deduplication(target_file, lib_root)
            self.assertTrue(ok)
            self.assertFalse(target_file.exists())

            backup_file = lib_root / "_duplicados_backup" / "General" / "Artist" / "Album" / "01 - Song.m4a"
            self.assertTrue(backup_file.exists())
            self.assertEqual(backup_file.read_text(), "old version")


class TestPicardTelegramMenu(unittest.TestCase):
    def test_build_plugins_menu(self):
        from modules.telegram import build_plugins_menu
        config = {
            "auto_romanizer": True,
            "lrclib_lyrics": False,
            "lastfm": True,
        }
        text, markup = build_plugins_menu(config)
        self.assertIn("Configuración de Plugins de Picard", text)
        self.assertIn("☑️", text)
        self.assertIn("⬜", text)
        self.assertIn("inline_keyboard", markup)

        # Check invert and close buttons exist
        bottom_row = markup["inline_keyboard"][-1]
        self.assertEqual(bottom_row[0]["callback_data"], "pcfg:invert")
        self.assertEqual(bottom_row[1]["callback_data"], "pcfg:close")

    def test_state_tracker_plugins_toggle(self):
        import tempfile
        from modules.state import StateTracker
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            path = tf.name
        try:
            tracker = StateTracker(path)
            plugins = tracker.get_picard_plugins()
            self.assertTrue(plugins["auto_romanizer"])
            self.assertFalse(plugins["playlist"])

            # Toggle playlist
            new_val = tracker.toggle_picard_plugin("playlist")
            self.assertTrue(new_val)
            self.assertTrue(tracker.get_picard_plugins()["playlist"])

            # Toggle back
            new_val = tracker.toggle_picard_plugin("playlist")
            self.assertFalse(new_val)
            self.assertFalse(tracker.get_picard_plugins()["playlist"])
        finally:
            Path(path).unlink(missing_ok=True)

    def test_bot_listener_plugins_command_and_callback(self):
        import tempfile
        from modules.state import StateTracker
        from modules.telegram import TelegramBotListener
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            path = tf.name
        try:
            tracker = StateTracker(path)
            mock_sync = MagicMock()
            mock_status = MagicMock(return_value="Status OK")
            listener = TelegramBotListener(
                bot_token="test_token",
                authorized_chat_ids=["123"],
                on_sync_command=mock_sync,
                on_status_command=mock_status,
                state_tracker=tracker,
            )
            listener.send_reply = MagicMock(return_value=456)
            listener.answer_callback_query = MagicMock()
            listener.edit_message_reply_markup = MagicMock(return_value=True)

            # 1. Test /plugins command
            msg_update = {
                "message": {
                    "chat": {"id": 123},
                    "text": "/plugins",
                }
            }
            listener.handle_update(msg_update)
            listener.send_reply.assert_called_once()
            args, kwargs = listener.send_reply.call_args
            self.assertEqual(args[0], "123")
            self.assertIn("Configuración de Plugins de Picard", args[1])
            self.assertIn("inline_keyboard", kwargs["reply_markup"])

            # 2. Test callback query toggle
            cq_update = {
                "callback_query": {
                    "id": "cq_pcfg_1",
                    "message": {"chat": {"id": 123}, "message_id": 456},
                    "data": "pcfg:playlist",
                }
            }
            listener.handle_update(cq_update)
            listener.answer_callback_query.assert_called_with("cq_pcfg_1", text="M3U Playlist: Activado")
            listener.edit_message_reply_markup.assert_called_once()
            self.assertTrue(tracker.get_picard_plugins()["playlist"])

            # 3. Test callback query close
            cq_close = {
                "callback_query": {
                    "id": "cq_pcfg_close",
                    "message": {"chat": {"id": 123}, "message_id": 456},
                    "data": "pcfg:close",
                }
            }
            listener.handle_update(cq_close)
            listener.answer_callback_query.assert_called_with("cq_pcfg_close", text="💾 Configuración guardada")
        finally:
            Path(path).unlink(missing_ok=True)

    def test_find_matching_existing_album_and_bracket_naming(self, tmp_path=Path("./test_picard_merge")):
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            from modules.picard_engine import PicardEngine

            artist_dir = tmp_path / "J-Music" / "Yorushika"
            artist_dir.mkdir(parents=True, exist_ok=True)

            # Create an existing album [2019] - Elma with a song
            elma_dir = artist_dir / "[2019] - Elma"
            elma_dir.mkdir(parents=True, exist_ok=True)
            (elma_dir / "01 - Train window.m4a").write_bytes(b"dummy")

            # Create existing [2023] - Magic Lantern with track Chinokate
            ml_dir = artist_dir / "[2023] - Magic Lantern"
            ml_dir.mkdir(parents=True, exist_ok=True)
            (ml_dir / "04 - Chinokate.m4a").write_bytes(b"dummy")

            # 1. Direct album match: 'Elma' should match '[2019] - Elma'
            matched = PicardEngine.find_matching_existing_album(artist_dir, "Elma")
            self.assertIsNotNone(matched)
            self.assertEqual(matched.name, "[2019] - Elma")

            # 2. Match with single suffix: 'Elma (single)' should also match '[2019] - Elma'
            matched_single = PicardEngine.find_matching_existing_album(artist_dir, "Elma (single)")
            self.assertIsNotNone(matched_single)
            self.assertEqual(matched_single.name, "[2019] - Elma")

            # 3. Distinct single should NOT merge into a studio album with a different name
            loose_track = tmp_path / "01 - Chinokate.m4a"
            loose_track.write_bytes(b"dummy")
            matched_track = PicardEngine.find_matching_existing_album(
                artist_dir, "Chinokate (single)", audio_files=[loose_track]
            )
            self.assertIsNone(matched_track)

            # 4. Brand new single: 'Play Sick (single)' should NOT match any existing album
            new_track = tmp_path / "01 - Play Sick.m4a"
            new_track.write_bytes(b"dummy")
            no_match = PicardEngine.find_matching_existing_album(
                artist_dir, "Play Sick (single)", audio_files=[new_track]
            )
            self.assertIsNone(no_match)

            # 5. Evaluate naming script should always format new items with [YYYY] -
            rel_folder, filename = PicardEngine.evaluate_naming_script(
                artist="Yorushika",
                album="Play Sick (single)",
                title="Play Sick",
                date="2025",
            )
            self.assertTrue(rel_folder.startswith("J-Music/Yorushika/[2025] - Play Sick (single)"))
        finally:
            if tmp_path.exists():
                shutil.rmtree(tmp_path, ignore_errors=True)


class TestDiscardAndIgnoreFeature(unittest.TestCase):
    def setUp(self):
        self.tmp_state = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.tmp_state.close()
        self.tracker = StateTracker(self.tmp_state.name)

    def tearDown(self):
        Path(self.tmp_state.name).unlink(missing_ok=True)

    def test_state_tracker_ignored_crud(self):
        # 1. Add ignored track
        added = self.tracker.add_ignored("7/13", "suis from Yorushika")
        self.assertTrue(added)
        # Duplicate should return False
        self.assertFalse(self.tracker.add_ignored("7/13", "suis from Yorushika"))

        # 2. Check is_ignored (case and symbol insensitive)
        self.assertTrue(self.tracker.is_ignored("7/13"))
        self.assertTrue(self.tracker.is_ignored("7/13", "suis"))
        self.assertTrue(self.tracker.is_ignored("7/13", "suis from Yorushika"))
        self.assertFalse(self.tracker.is_ignored("Crime & Punishment"))

        # 3. Add second item
        self.tracker.add_ignored("Tokyo Flash - TEMPLIME Remix", "Vaundy")
        ignored = self.tracker.get_ignored()
        self.assertEqual(len(ignored), 2)

        # 4. Remove by name
        removed = self.tracker.remove_ignored("7/13")
        self.assertTrue(removed)
        self.assertFalse(self.tracker.is_ignored("7/13"))
        self.assertEqual(len(self.tracker.get_ignored()), 1)

        # 5. Clear all
        cleared = self.tracker.clear_ignored()
        self.assertEqual(cleared, 1)
        self.assertEqual(len(self.tracker.get_ignored()), 0)

    @patch("modules.syncer.YouTubeMusicResolver")
    def test_process_playlist_text_filters_ignored(self, mock_resolver_cls):
        from modules.syncer import process_playlist_text
        mock_resolver = MagicMock()
        mock_resolver_cls.return_value = mock_resolver

        # Set up ignored track
        self.tracker.add_ignored("Ignored Song")

        raw_comment = "Tracks not found in library: Ignored Song, Active Song"
        mock_tg = MagicMock()
        mock_nav = MagicMock()
        mock_nav.find_best_match.return_value = None  # None in library

        mock_res = ResolutionResult()
        mock_res.albums = [
            ResolvedAlbum(
                url="https://music.youtube.com/playlist?list=OLAK1",
                album_name="Active Album",
                artist_name="Artist",
                tracks=["Active Song"],
            )
        ]
        mock_resolver.resolve.return_value = mock_res

        processed = process_playlist_text(
            playlist_name="Test PL",
            raw_text=raw_comment,
            navidrome=mock_nav,
            telegram=mock_tg,
            state_tracker=self.tracker,
        )
        self.assertTrue(processed)
        # Verify resolver was called ONLY with 'Active Song'
        called_tracks = mock_resolver.resolve.call_args[0][0]
        self.assertEqual(len(called_tracks), 1)
        self.assertEqual(called_tracks[0].title, "Active Song")

    @patch("modules.syncer.YouTubeMusicResolver")
    def test_catdays_resolved_tracks_deduplication(self, mock_resolver_cls):
        """Verifies that an album whose search query was '7/13' but resolved to '猫日 - Catdays'
        is properly detected as existing in Navidrome when Navidrome has '猫日 - Catdays'."""
        from modules.syncer import process_playlist_text
        mock_resolver = MagicMock()
        mock_resolver_cls.return_value = mock_resolver

        mock_nav = MagicMock()
        # Navidrome has album Catdays
        mock_nav.find_album_match.return_value = {"id": "alb_catdays", "name": "Catdays"}
        # Navidrome contains track '猫日 - Catdays'
        mock_nav.get_album_tracks.return_value = [
            {"id": "song_123", "title": "猫日 - Catdays", "artist": "suis"}
        ]
        mock_nav.find_best_match.return_value = None

        alb = ResolvedAlbum(
            url="https://music.youtube.com/playlist?list=OLAK_cat",
            album_name="Catdays",
            artist_name="suis from Yorushika",
            tracks=["7/13"],
            resolved_tracks=["猫日 - Catdays"],
        )

        mock_res = ResolutionResult()
        mock_res.albums = [alb]
        mock_resolver.resolve.return_value = mock_res

        mock_tg = MagicMock()
        processed = process_playlist_text(
            playlist_name="Test PL",
            raw_text="Tracks not found in library: 7/13",
            navidrome=mock_nav,
            telegram=mock_tg,
            state_tracker=self.tracker,
        )
        self.assertTrue(processed)
        # Because '猫日 - Catdays' matched Navidrome, Catdays was detected as complete and omitted from pending!
        mock_tg.send_notification.assert_not_called()

    def test_telegram_discard_and_restore(self):
        mock_discard = MagicMock()
        mock_restore = MagicMock()
        listener = TelegramBotListener(
            bot_token="dummy_token",
            authorized_chat_ids=["123"],
            on_sync_command=MagicMock(),
            on_status_command=MagicMock(),
            on_discard_command=mock_discard,
            on_restore_command=mock_restore,
            state_tracker=self.tracker,
        )

        # Test /descartar
        update_discard = {
            "message": {
                "chat": {"id": 123},
                "text": "/descartar 1",
            }
        }
        listener.handle_update(update_discard)
        mock_discard.assert_called_once_with("123", None, "1")

        # Test /restaurar
        update_restore = {
            "message": {
                "chat": {"id": 123},
                "text": "/restaurar Tokyo Flash",
            }
        }
        listener.handle_update(update_restore)
        mock_restore.assert_called_once_with("123", None, "Tokyo Flash")


if __name__ == "__main__":
    unittest.main()



