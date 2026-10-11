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
