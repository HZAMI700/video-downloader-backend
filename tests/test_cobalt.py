import pytest
from unittest.mock import patch, MagicMock
from app.services.cobalt_service import extract_cobalt_media


def test_extract_cobalt_media_tunnel_success():
    fake_response = {
        "status": "tunnel",
        "url": "http://188.212.125.198:9000/tunnel?id=test",
        "filename": "tiktok_scout2015_6718335390845095173.mp4",
        "thumb": "https://p16.tiktokcdn.com/test.jpg"
    }

    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fake_response
        mock_post.return_value = mock_resp

        res = extract_cobalt_media("https://www.tiktok.com/@scout2015/video/6718335390845095173")
        assert res is not None
        assert res["platform"] == "TikTok"
        assert res["direct_media_url"] == "http://188.212.125.198:9000/tunnel?id=test"
        assert res["thumbnail"] == "https://p16.tiktokcdn.com/test.jpg"
        assert len(res["formats"]) == 3
        format_ids = [f["format_id"] for f in res["formats"]]
        assert "video_1080p" in format_ids
        assert "video_720p" in format_ids
        assert "audio_mp3" in format_ids


def test_extract_cobalt_media_facebook_redirect():
    fake_response = {
        "status": "redirect",
        "url": "https://video-fra3-1.xx.fbcdn.net/test.mp4",
        "filename": "facebook_10153231379946729.mp4"
    }

    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fake_response
        mock_post.return_value = mock_resp

        res = extract_cobalt_media("https://www.facebook.com/reel/10153231379946729")
        assert res is not None
        assert res["platform"] == "Facebook"
        assert res["direct_media_url"] == "https://video-fra3-1.xx.fbcdn.net/test.mp4"


def test_extract_cobalt_media_error_graceful():
    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_post.return_value = mock_resp

        res = extract_cobalt_media("https://www.tiktok.com/@invalid/video/123")
        assert res is None
