import pytest
from app.services.notube_service import (
    find_live_notube_server,
    map_format_to_notube,
    extract_youtube_metadata_via_oembed,
    convert_youtube_via_notube,
)


def test_map_format_to_notube():
    assert map_format_to_notube("audio_mp3") == "mp3"
    assert map_format_to_notube("mp3") == "mp3"
    assert map_format_to_notube("audio_wav") == "wav"
    assert map_format_to_notube("audio_m4a") == "m4a"
    assert map_format_to_notube("video_1080p") == "mp4hd"
    assert map_format_to_notube("video_720p") == "mp4"
    assert map_format_to_notube("best_video") == "mp4hd"


def test_find_live_notube_server():
    server = find_live_notube_server()
    assert isinstance(server, int)
    assert 40 <= server <= 76


def test_extract_youtube_metadata_via_oembed():
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    data = extract_youtube_metadata_via_oembed(url)
    assert data is not None
    assert data["platform"] == "YouTube"
    assert "Never Gonna Give You Up" in data["title"]
    assert len(data["formats"]) >= 4
    assert any(f["format_id"] == "audio_mp3" for f in data["formats"])
    assert any(f["format_id"] == "video_1080p" for f in data["formats"])
