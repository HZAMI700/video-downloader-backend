import io
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.transcription_service import (
    format_timestamp_srt,
    format_timestamp_vtt,
    generate_srt,
    generate_vtt,
)
from app.services.transcription_manager import transcription_manager, TranscriptionJob

client = TestClient(app)


def test_format_timestamps():
    assert format_timestamp_srt(0.0) == "00:00:00,000"
    assert format_timestamp_srt(65.123) == "00:01:05,123"
    assert format_timestamp_srt(3661.050) == "01:01:01,050"

    assert format_timestamp_vtt(0.0) == "00:00:00.000"
    assert format_timestamp_vtt(65.123) == "00:01:05.123"


def test_generate_srt_and_vtt():
    segments = [
        {"id": 1, "start": 1.0, "end": 4.5, "text": "Hello world."},
        {"id": 2, "start": 4.5, "end": 8.0, "text": "This is an automated transcription."},
    ]

    srt_out = generate_srt(segments)
    assert "1\n00:00:01,000 --> 00:00:04,500\nHello world." in srt_out
    assert "2\n00:00:04,500 --> 00:00:08,000\nThis is an automated transcription." in srt_out

    vtt_out = generate_vtt(segments)
    assert vtt_out.startswith("WEBVTT\n\n")
    assert "00:00:01.000 --> 00:00:04.500" in vtt_out


def test_upload_invalid_extension():
    fake_file = io.BytesIO(b"fake executable content")
    response = client.post(
        "/api/transcribe/upload",
        files={"file": ("malware.exe", fake_file, "application/octet-stream")},
        data={"language": "auto"}
    )
    assert response.status_code == 400
    assert "Unsupported media format" in response.json()["detail"]


def test_transcribe_url_ssrf_protection():
    # Attempt SSRF to local IP
    response = client.post(
        "/api/transcribe/url",
        json={"url": "http://127.0.0.1:8000/internal", "language": "auto"}
    )
    assert response.status_code == 400
    assert "Invalid or prohibited URL" in response.json()["detail"]


def test_transcribe_url_success():
    response = client.post(
        "/api/transcribe/url",
        json={"url": "https://example.com/audio.mp3", "language": "en"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "job_id" in data
    assert data["status"] == "queued"


def test_transcription_status_and_export():
    # Create a completed job in the manager directly to test status & export endpoints
    job_id = "tx_test_12345"
    job = TranscriptionJob(
        job_id=job_id,
        source_type="file",
        filename="test_interview.mp3",
        language="en"
    )
    job.status = "completed"
    job.progress = 100
    job.stage = "completed"
    job.text = "Hello and welcome to Playzy Transcriber."
    job.segments = [
        {"id": 0, "start": 0.0, "end": 3.0, "text": "Hello and welcome"},
        {"id": 1, "start": 3.0, "end": 5.5, "text": "to Playzy Transcriber."},
    ]
    transcription_manager._jobs[job_id] = job

    # Test status endpoint
    status_res = client.get(f"/api/transcribe/{job_id}")
    assert status_res.status_code == 200
    data = status_res.json()
    assert data["job_id"] == job_id
    assert data["status"] == "completed"
    assert data["text"] == "Hello and welcome to Playzy Transcriber."
    assert len(data["segments"]) == 2

    # Test export as TXT
    txt_res = client.get(f"/api/transcribe/{job_id}/export?format=txt")
    assert txt_res.status_code == 200
    assert "text/plain" in txt_res.headers["content-type"]
    assert "Hello and welcome to Playzy Transcriber." in txt_res.text

    # Test export as SRT
    srt_res = client.get(f"/api/transcribe/{job_id}/export?format=srt")
    assert srt_res.status_code == 200
    assert "00:00:00,000 --> 00:00:03,000" in srt_res.text

    # Test export as VTT
    vtt_res = client.get(f"/api/transcribe/{job_id}/export?format=vtt")
    assert vtt_res.status_code == 200
    assert vtt_res.text.startswith("WEBVTT")


def test_transcription_not_found():
    res = client.get("/api/transcribe/tx_nonexistent")
    assert res.status_code == 404
