def test_healthz_endpoint(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "video-downloader-backend"
    assert "timestamp" in data


def test_capabilities_endpoint(client):
    response = client.get("/api/capabilities")
    assert response.status_code == 200
    data = response.json()
    assert data["backend"] == "ready"
    assert "ytdlp_version" in data
    assert isinstance(data["ffmpeg_available"], bool)
    assert data["max_file_size_mb"] > 0
    assert len(data["supported_extractors"]) > 0
    assert "features" in data
    assert "audio_extraction" in data["features"]
    assert "video_muxing" in data["features"]


def test_root_endpoint(client):
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert data["health"] == "/healthz"
