import io
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_translate_endpoint():
    # Test translate endpoint with basic text
    res = client.post("/api/transcribe/translate", json={
        "text": "Hello world",
        "target_language": "Spanish"
    })
    # Status should be 200 (if Groq key works or fallbacks) or valid response
    assert res.status_code == 200
    data = res.json()
    assert "translated_text" in data
    assert data["target_language"] == "Spanish"


def test_translate_empty_text():
    res = client.post("/api/transcribe/translate", json={
        "text": "   ",
        "target_language": "Spanish"
    })
    assert res.status_code == 400


def test_audio_boost_validation():
    # Invalid extension
    fake_file = io.BytesIO(b"fake txt content")
    res = client.post(
        "/api/audio/boost",
        files={"file": ("test.txt", fake_file, "text/plain")},
        data={"boost_percent": 200}
    )
    assert res.status_code == 400
