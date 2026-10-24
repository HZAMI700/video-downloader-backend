import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, HttpUrl

from app.config import settings
from app.security import sanitize_filename, is_safe_url
from app.services.transcription_manager import transcription_manager
from app.services.transcription_service import generate_srt, generate_vtt

logger = logging.getLogger("video_downloader.routes.transcription")

router = APIRouter(prefix="/api/transcribe", tags=["Transcription"])

# Permitted audio and video extensions
ALLOWED_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma",
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".flv", ".wmv", ".3gp"
}


class UrlTranscribeRequest(BaseModel):
    url: str
    language: Optional[str] = "auto"
    diarization: bool = False


@router.post("/upload")
async def upload_and_transcribe(
    file: UploadFile = File(...),
    language: Optional[str] = Form("auto"),
    diarization: bool = Form(False),
):
    """
    Upload an audio or video file to queue for AI transcription.
    Enforces maximum file size limit and supported media formats.
    """
    raw_filename = file.filename or "uploaded_media"
    sanitized_name = sanitize_filename(raw_filename)
    ext = Path(sanitized_name).suffix.lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported media format '{ext}'. Supported formats: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    temp_dir = settings.temp_dir_path
    unique_id = uuid.uuid4().hex[:10]
    temp_file_path = temp_dir / f"upload_{unique_id}_{sanitized_name}"

    try:
        max_bytes = settings.MAX_TRANSCRIPTION_FILE_SIZE_MB * 1024 * 1024
        bytes_written = 0

        with open(temp_file_path, "wb") as buffer:
            while chunk := await file.read(1024 * 1024):  # 1MB chunks
                bytes_written += len(chunk)
                if bytes_written > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds maximum allowed size of {settings.MAX_TRANSCRIPTION_FILE_SIZE_MB} MB."
                    )
                buffer.write(chunk)

    except HTTPException:
        if temp_file_path.exists():
            temp_file_path.unlink(missing_ok=True)
        raise
    except Exception as e:
        if temp_file_path.exists():
            temp_file_path.unlink(missing_ok=True)
        logger.error(f"Error saving uploaded file: {e}")
        raise HTTPException(status_code=500, detail="Failed to receive file upload.")

    job = transcription_manager.create_file_job(
        source_path=temp_file_path,
        filename=sanitized_name,
        language=language,
        diarization=diarization,
    )

    return {
        "job_id": job.job_id,
        "status": job.status,
        "filename": sanitized_name,
        "message": "File received and queued for transcription."
    }


@router.post("/url")
async def transcribe_from_url(req: UrlTranscribeRequest):
    """
    Queue media from a URL (audio/video direct link or supported media platforms) for AI transcription.
    """
    url_clean = req.url.strip()
    if not is_safe_url(url_clean):
        raise HTTPException(status_code=400, detail="Invalid or prohibited URL format.")

    job = transcription_manager.create_url_job(
        url=url_clean,
        language=req.language,
        diarization=req.diarization,
    )

    return {
        "job_id": job.job_id,
        "status": job.status,
        "url": url_clean,
        "message": "Media URL queued for transcription."
    }


@router.get("/{job_id}")
async def get_transcription_status(job_id: str):
    """
    Get current progress, status, and results for a transcription job.
    """
    job = transcription_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Transcription job not found or has expired.")
    return job.to_dict()


@router.get("/{job_id}/export")
async def export_transcript(job_id: str, format: str = Query("txt", pattern="^(txt|srt|vtt)$")):
    """
    Export completed transcript as plain TXT, SubRip SRT subtitles, or WebVTT subtitles.
    """
    job = transcription_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Transcription job not found or has expired.")

    if job.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Transcription is not complete (current status: {job.status})."
        )

    base_name = sanitize_filename(Path(job.filename).stem or "transcript")
    format_lower = format.lower()

    if format_lower == "txt":
        content = job.text or ""
        media_type = "text/plain; charset=utf-8"
        filename = f"{base_name}.txt"
    elif format_lower == "srt":
        content = generate_srt(job.segments, fallback_text=job.text)
        media_type = "application/x-subrip; charset=utf-8"
        filename = f"{base_name}.srt"
    elif format_lower == "vtt":
        content = generate_vtt(job.segments, fallback_text=job.text)
        media_type = "text/vtt; charset=utf-8"
        filename = f"{base_name}.vtt"
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported export format '{format}'.")

    return PlainTextResponse(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
