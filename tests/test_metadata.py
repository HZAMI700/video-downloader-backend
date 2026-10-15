from unittest.mock import patch
from fastapi import HTTPException


def test_metadata_ssrf_blocked(client):
    response = client.post("/api/metadata", json={"url": "http://127.0.0.1:8000/media"})
    assert response.status_code == 400
    assert "restricted" in response.json()["detail"].lower() or "local" in response.json()["detail"].lower()


def test_metadata_success_mocked(client):
    mock_data = {
        "id": "mock123",
        "title": "Mock Video Title",
        "thumbnail": "https://example.com/thumb.jpg",
        "duration": 180,
        "duration_formatted": "03:00",
        "uploader": "Test Channel",
        "view_count": 12345,
        "platform": "YouTube",
        "original_url": "https://www.youtube.com/watch?v=mock123",
        "formats": [
            {
                "format_id": "video_1080p",
                "type": "video",
                "label": "1080p MP4",
                "resolution": "1080p",
                "ext": "mp4",
                "quality_note": "High Definition",
                "requires_ffmpeg": True,
                "available": True,
                "approx_size_bytes": 10485760
            },
            {
                "format_id": "audio_mp3",
                "type": "audio",
                "label": "MP3 Audio",
                "resolution": "audio",
                "ext": "mp3",
                "quality_note": "Highest Quality",
                "requires_ffmpeg": True,
                "available": True,
                "approx_size_bytes": 3145728
            }
        ],
        "ffmpeg_available": True
    }

    with patch("app.routes.metadata.extract_media_metadata", return_value=mock_data):
        response = client.post("/api/metadata", json={"url": "https://www.youtube.com/watch?v=mock123"})
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Mock Video Title"
        assert len(data["formats"]) == 2
        assert data["formats"][0]["format_id"] == "video_1080p"


def test_metadata_extractor_error(client):
    with patch("app.routes.metadata.extract_media_metadata", side_effect=HTTPException(status_code=403, detail="Private video")):
        response = client.post("/api/metadata", json={"url": "https://www.youtube.com/watch?v=private"})
        assert response.status_code == 403
        assert response.json()["detail"] == "Private video"


def test_metadata_facebook_float_duration(client):
    mock_fb_data = {
        "id": "10153231379946729",
        "title": "Facebook Video",
        "thumbnail": "https://example.com/fb_thumb.jpg",
        "duration": 74.304,  # Float duration that previously triggered ValidationError
        "duration_formatted": "01:14",
        "uploader": "Mark",
        "view_count": 50000,
        "platform": "Facebook",
        "original_url": "https://www.facebook.com/watch/?v=10153231379946729",
        "formats": [
            {
                "format_id": "video_1080p",
                "type": "video",
                "label": "MP4 1080p Full HD",
                "resolution": "1080p",
                "ext": "mp4",
                "quality_note": "High Definition",
                "requires_ffmpeg": False,
                "available": True,
                "approx_size_bytes": 10485760
            }
        ],
        "ffmpeg_available": True
    }
    with patch("app.routes.metadata.extract_media_metadata", return_value=mock_fb_data):
        response = client.post("/api/metadata", json={"url": "https://www.facebook.com/watch/?v=10153231379946729"})
        assert response.status_code == 200
        data = response.json()
        assert data["platform"] == "Facebook"
        assert data["duration"] == 74.304 or data["duration"] == 74


def test_metadata_tiktok_success(client):
    mock_tt_data = {
        "id": "7106594312292453678",
        "title": "Viral TikTok",
        "thumbnail": "https://example.com/tt_thumb.jpg",
        "duration": 15,
        "duration_formatted": "00:15",
        "uploader": "tiktok_creator",
        "view_count": 250000,
        "platform": "TikTok",
        "original_url": "https://www.tiktok.com/@tiktok/video/7106594312292453678",
        "formats": [
            {
                "format_id": "video_1080p",
                "type": "video",
                "label": "MP4 1080p Full HD (No Watermark)",
                "resolution": "1080p",
                "ext": "mp4",
                "quality_note": "Crystal High Bitrate Without Watermark",
                "requires_ffmpeg": False,
                "available": True,
                "approx_size_bytes": 15000000
            }
        ],
        "ffmpeg_available": True
    }
    with patch("app.routes.metadata.extract_media_metadata", return_value=mock_tt_data):
        response = client.post("/api/metadata", json={"url": "https://www.tiktok.com/@tiktok/video/7106594312292453678"})
        assert response.status_code == 200
        data = response.json()
        assert data["platform"] == "TikTok"
        assert "No Watermark" in data["formats"][0]["label"]


def test_classify_extraction_error_messages():
    from app.services.ytdlp_service import classify_extraction_error
    e_priv = Exception("ERROR: [youtube] 123: Private video")
    http_e = classify_extraction_error(e_priv, "YouTube", "https://youtube.com/watch?v=123")
    assert http_e.status_code == 403
    assert "private" in http_e.detail.lower()

    e_bot = Exception("ERROR: [TikTok] 123: Your IP address is blocked from accessing this post")
    http_bot = classify_extraction_error(e_bot, "TikTok", "https://tiktok.com/@u/video/123")
    assert http_bot.status_code == 503
    assert "security challenge" in http_bot.detail.lower()

    e_ig = Exception("ERROR: [Instagram] ABC: Instagram sent an empty media response. Check if this post is accessible in your browser without being logged-in.")
    http_ig = classify_extraction_error(e_ig, "Instagram", "https://instagram.com/p/ABC/")
    assert http_ig.status_code == 403
    assert "authentication" in http_ig.detail.lower()


def test_metadata_instagram_photo_post(client):
    mock_ig_photo = {
        "id": "DF2qM62tpn8",
        "title": "Instagram Photo Post",
        "thumbnail": "https://example.com/ig_photo.jpg",
        "duration": None,
        "duration_formatted": None,
        "uploader": "photographer",
        "view_count": 1000,
        "platform": "Instagram",
        "original_url": "https://www.instagram.com/p/DF2qM62tpn8/",
        "formats": [
            {
                "format_id": "image_orig",
                "type": "image",
                "label": "Original Photo (High Definition)",
                "resolution": "1080x1350",
                "ext": "jpg",
                "quality_note": "Original Image File",
                "requires_ffmpeg": False,
                "available": True,
                "approx_size_bytes": 2097152,
                "direct_url": "https://example.com/ig_photo_orig.jpg"
            },
            {
                "format_id": "image_hd",
                "type": "image",
                "label": "JPEG Image (Standard Quality)",
                "resolution": "Standard",
                "ext": "jpg",
                "quality_note": "Compressed Web JPEG",
                "requires_ffmpeg": False,
                "available": True,
                "approx_size_bytes": 1048576,
                "direct_url": "https://example.com/ig_photo_hd.jpg"
            }
        ],
        "ffmpeg_available": True,
        "direct_media_url": "https://example.com/ig_photo_orig.jpg"
    }
    with patch("app.routes.metadata.extract_media_metadata", return_value=mock_ig_photo):
        response = client.post("/api/metadata", json={"url": "https://www.instagram.com/p/DF2qM62tpn8/"})
        assert response.status_code == 200
        data = response.json()
        assert data["platform"] == "Instagram"
        assert len(data["formats"]) == 2
        assert data["formats"][0]["type"] == "image"
        assert data["formats"][0]["format_id"] == "image_orig"
        assert data["direct_media_url"] == "https://example.com/ig_photo_orig.jpg"


def test_find_cookies_file(tmp_path, monkeypatch):
    from app.services.ytdlp_service import find_cookies_file
    # Test with COOKIES_TEXT environment variable
    monkeypatch.setenv("COOKIES_TEXT", "# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t1790000000\tsessionid\t123456")
    cookie_path = find_cookies_file()
    assert cookie_path is not None
    assert "imported_cookies.txt" in cookie_path
    with open(cookie_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "sessionid" in content

    # Clean up env var
    monkeypatch.delenv("COOKIES_TEXT", raising=False)



