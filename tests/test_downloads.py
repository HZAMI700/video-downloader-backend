import os
import time
from pathlib import Path
from unittest.mock import patch
from app.config import settings
from app.services.job_manager import job_manager


def test_create_download_job(client):
    with patch.object(job_manager, "execute_download"):
        response = client.post("/api/downloads", json={
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "format_id": "video_720p"
        })
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert data["status"] in ("queued", "processing")


def test_get_download_status_not_found(client):
    response = client.get("/api/downloads/non-existent-uuid")
    assert response.status_code == 404


def test_get_download_status_success(client):
    job = job_manager.create_job("https://example.com/video", "video_720p")
    job.status = "processing"
    job.progress_percent = 45.5
    job.downloaded_bytes = 4500000
    job.total_bytes = 10000000

    response = client.get(f"/api/downloads/{job.job_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == job.job_id
    assert data["status"] == "processing"
    assert data["progress_percent"] == 45.5
    assert data["downloaded_bytes"] == 4500000


def test_file_delivery_lifecycle(client):
    job = job_manager.create_job("https://example.com/video", "best_video")

    # While still queued / processing, file endpoint returns 409 Conflict
    response = client.get(f"/api/downloads/{job.job_id}/file")
    assert response.status_code == 409

    # Create dummy completed file in temp dir
    test_file = settings.temp_dir_path / f"{job.job_id}_test_video.mp4"
    test_file.write_bytes(b"dummy video data 12345")

    job.status = "completed"
    job.file_path = str(test_file)
    job.filename = "test_video.mp4"
    job.file_size = len(b"dummy video data 12345")
    job.content_type = "video/mp4"

    # Now download file
    response = client.get(f"/api/downloads/{job.job_id}/file")
    assert response.status_code == 200
    assert response.content == b"dummy video data 12345"
    assert "attachment; filename=\"test_video.mp4\"" in response.headers.get("content-disposition", "")


def test_file_delivery_path_traversal_blocked(client):
    job = job_manager.create_job("https://example.com/video", "best_video")
    job.status = "completed"
    # Point outside temp directory
    job.file_path = str(Path(__file__).resolve().parent / "conftest.py")
    job.filename = "conftest.py"

    response = client.get(f"/api/downloads/{job.job_id}/file")
    assert response.status_code == 403


def test_delete_download_job(client):
    job = job_manager.create_job("https://example.com/video", "best_video")
    test_file = settings.temp_dir_path / f"{job.job_id}_test.mp4"
    test_file.write_bytes(b"temp data")
    job.file_path = str(test_file)

    assert test_file.exists()

    # Delete job
    response = client.delete(f"/api/downloads/{job.job_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"

    # File should be removed
    assert not test_file.exists()
    assert job_manager.get_job(job.job_id) is None


def test_cleanup_expired_jobs():
    job = job_manager.create_job("https://example.com/video", "best_video")
    test_file = settings.temp_dir_path / f"{job.job_id}_old.mp4"
    test_file.write_bytes(b"old data")
    job.file_path = str(test_file)

    # Artificially age the job
    job.created_at = time.time() - (settings.DOWNLOAD_TTL_SECONDS + 100)

    job_manager.cleanup_expired()

    # Should be purged
    assert not test_file.exists()
    assert job_manager.get_job(job.job_id) is None
