#!/usr/bin/env python3
"""
Data structures and models for the ListenBrainz Notifier.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class MissingTrack:
    title: str
    artist: str = ""


@dataclass
class ResolvedAlbum:
    url: str
    album_name: str
    artist_name: str
    tracks: List[str] = field(default_factory=list)
    resolved_tracks: List[str] = field(default_factory=list)
    first_index: int = 999999
    is_single: bool = False
    cover_url: Optional[str] = None
    is_partial: bool = False
    nav_present_tracks: int = 0
    nav_total_tracks: int = 0
    year: Optional[str] = None


@dataclass
class ResolvedTrack:
    url: str
    title: str
    artist: str
    first_index: int = 999999
    cover_url: Optional[str] = None


@dataclass
class ResolutionResult:
    albums: List[ResolvedAlbum] = field(default_factory=list)
    tracks: List[ResolvedTrack] = field(default_factory=list)
    unresolved: List[MissingTrack] = field(default_factory=list)

    # Batching / pagination fields
    total_albums_detected: int = 0
    total_tracks_detected: int = 0
    batch_number: int = 1
    total_batches: int = 1
    batch_size: int = 0

    def __post_init__(self):
        if self.total_albums_detected == 0 and self.albums:
            self.total_albums_detected = len(self.albums)
        if self.total_tracks_detected == 0 and self.tracks:
            self.total_tracks_detected = len(self.tracks)

    @property
    def total_links(self) -> int:
        return len(self.albums) + len(self.tracks)

    @property
    def all_urls(self) -> List[str]:
        urls = [alb.url for alb in self.albums]
        urls.extend([trk.url for trk in self.tracks])
        return urls


@dataclass
class DownloadJob:
    job_id: str
    chat_id: str
    thread_id: Optional[str]
    title: str
    albums: List[ResolvedAlbum] = field(default_factory=list)
    tracks: List[ResolvedTrack] = field(default_factory=list)
    source_desc: str = ""


@dataclass
class TelegramTarget:
    chat_id: str
    thread_id: Optional[str] = None
