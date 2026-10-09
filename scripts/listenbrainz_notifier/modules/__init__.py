#!/usr/bin/env python3
"""
ListenBrainz Notifier Modules Package
"""

from .discoverer import YouTubeMusicDiscoverer
from .downloader import AlbumProgressStreamer, DownloadQueue, MusicDownloader
from .enricher import ListenBrainzEnricher
from .models import (
    DownloadJob,
    MissingTrack,
    ResolutionResult,
    ResolvedAlbum,
    ResolvedTrack,
    TelegramTarget,
)
from .navidrome import NavidromeClient
from .parser import TrackParser
from .resolver import UniversalResolver, YouTubeMusicResolver, parse_sync_args
from .auditor import IncompleteAlbumAuditor
from .consolidator import FolderConsolidator
from .nightly import NightlyDownloadManager
from .picard_engine import DEFAULT_PICARD_PLUGINS, PicardEngine
from .state import StateTracker
from .syncer import process_playlist_text, run_sync
from .telegram import (
    TelegramBotListener,
    TelegramSender,
    build_checklist_menu,
    build_discography_menu,
    build_plugins_menu,
)

__all__ = [
    "MissingTrack",
    "ResolvedAlbum",
    "ResolvedTrack",
    "ResolutionResult",
    "DownloadJob",
    "TelegramTarget",
    "TrackParser",
    "ListenBrainzEnricher",
    "YouTubeMusicResolver",
    "UniversalResolver",
    "parse_sync_args",
    "YouTubeMusicDiscoverer",
    "NavidromeClient",
    "MusicDownloader",
    "DownloadQueue",
    "AlbumProgressStreamer",
    "FolderConsolidator",
    "IncompleteAlbumAuditor",
    "NightlyDownloadManager",
    "TelegramSender",
    "TelegramBotListener",
    "build_discography_menu",
    "build_checklist_menu",
    "build_plugins_menu",
    "PicardEngine",
    "DEFAULT_PICARD_PLUGINS",
    "StateTracker",
    "process_playlist_text",
    "run_sync",
]
