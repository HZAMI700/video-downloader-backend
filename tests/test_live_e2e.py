import pytest
from app.services.ytdlp_service import extract_media_metadata
from app.services.job_manager import job_manager


def test_live_metadata_extraction_e2e():
    """Verify live extraction against a public direct media URL."""
    url = "https://www.w3schools.com/html/mov_bbb.mp4"
    data = extract_media_metadata(url)
    assert data["id"] == "mov_bbb"
    assert data["original_url"] == url
    assert len(data["formats"]) > 0


def test_live_download_pipeline_e2e():
    """Verify complete download pipeline with genuine media."""
    url = "https://www.w3schools.com/html/mov_bbb.mp4"
    job = job_manager.create_job(url=url, format_id="best_video")
    assert job.status == "queued"

    # Execute download
    job_manager.execute_download(job.job_id)

    assert job.status == "completed"
    assert job.progress_percent == 100.0
    assert job.file_size is not None and job.file_size > 0
    assert job.filename == "mov_bbb.mp4"
    assert job.content_type == "video/mp4"

    # Cleanup job
    assert job_manager.delete_job(job.job_id) is True
