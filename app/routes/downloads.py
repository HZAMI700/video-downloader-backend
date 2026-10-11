import os
from pathlib import Path
import threading
from typing import Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel
from app.config import settings
from app.security import validate_media_url
from app.services.job_manager import job_manager

router = APIRouter(prefix="/api/downloads", tags=["Downloads"])


class DownloadCreateRequest(BaseModel):
    url: str
    format_id: str = "best_video"


class DownloadCreateResponse(BaseModel):
    job_id: str
    status: str
    message: str


class DownloadStatusResponse(BaseModel):
    job_id: str
    url: str
    platform: Optional[str] = "Unknown"
    format_id: str
    status: str
    progress_percent: float
    downloaded_bytes: int
    total_bytes: Optional[int] = None
    speed_bytes_sec: Optional[float] = None
    eta_seconds: Optional[int] = None
    filename: Optional[str] = None
    file_size: Optional[int] = None
    content_type: Optional[str] = None
    error_message: Optional[str] = None
    created_at: float
    completed_at: Optional[float] = None


@router.post("", response_model=DownloadCreateResponse, status_code=202)
def create_download_job(payload: DownloadCreateRequest):
    """
    Queue a new media download job. Server validates URL and begins asynchronous processing.
    """
    valid_url = validate_media_url(payload.url)
    format_id = payload.format_id.strip() if payload.format_id else "best_video"

    job = job_manager.create_job(url=valid_url, format_id=format_id)

    # Spawn thread for downloading
    download_thread = threading.Thread(
        target=job_manager.execute_download,
        args=(job.job_id,),
        daemon=True,
        name=f"worker-{job.job_id[:8]}"
    )
    download_thread.start()

    return DownloadCreateResponse(
        job_id=job.job_id,
        status=job.status,
        message="Download job created and queued."
    )


@router.get("/{job_id}", response_model=DownloadStatusResponse)
def get_download_status(job_id: str):
    """
    Get real-time status and progress of a download job.
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Download job not found or has expired.")

    return DownloadStatusResponse(**job.to_dict())


@router.get("/{job_id}/file")
def download_completed_file(job_id: str):
    """
    Stream the completed download file to the client. Protected against path traversal.
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Download job not found or has expired.")

    if job.status == "failed":
        raise HTTPException(status_code=400, detail=job.error_message or "Download job failed.")

    if job.status != "completed":
        raise HTTPException(status_code=409, detail=f"Job is still {job.status}. File is not ready for download.")

    if not job.file_path or not os.path.exists(job.file_path):
        raise HTTPException(status_code=404, detail="File not found on disk. It may have expired or been removed.")

    # Prevent path traversal
    resolved_file = Path(job.file_path).resolve()
    resolved_temp_dir = settings.temp_dir_path.resolve()

    try:
        # Check if resolved_file is strictly inside resolved_temp_dir
        resolved_file.relative_to(resolved_temp_dir)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied: invalid file path.")

    filename = job.filename or "downloaded_media"
    media_type = job.content_type or "application/octet-stream"

    return FileResponse(
        path=str(resolved_file),
        filename=filename,
        media_type=media_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )


@router.delete("/{job_id}")
def delete_download_job(job_id: str):
    """
    Manually delete a job and clean up its temporary files immediately.
    """
    success = job_manager.delete_job(job_id)
    if not success:
        raise HTTPException(status_code=404, detail="Job not found or already deleted.")
    return {"status": "deleted", "job_id": job_id}
