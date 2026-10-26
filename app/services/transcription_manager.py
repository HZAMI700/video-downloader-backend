import asyncio
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Dict, Optional, Any, List
import yt_dlp
from app.config import settings
from app.security import sanitize_filename, is_safe_url
from app.services.transcription_service import (
    extract_compressed_audio,
    execute_transcription,
    generate_srt,
    generate_vtt,
)

logger = logging.getLogger("video_downloader.transcription_manager")


class TranscriptionJob:
    def __init__(
        self,
        job_id: str,
        source_type: str,
        filename: str,
        language: Optional[str] = None,
        diarization: bool = False,
        source_path: Optional[Path] = None,
        source_url: Optional[str] = None,
    ):
        self.job_id: str = job_id
        self.source_type: str = source_type  # "file" or "url"
        self.filename: str = filename
        self.language: Optional[str] = language
        self.diarization: bool = diarization
        self.source_path: Optional[Path] = source_path
        self.source_url: Optional[str] = source_url

        self.status: str = "queued"  # queued, processing, completed, failed
        self.progress: int = 0
        self.stage: str = "queued"   # queued, extracting_audio, transcribing, completed, failed
        self.error: Optional[str] = None

        self.text: str = ""
        self.segments: List[Dict[str, Any]] = []
        self.duration: float = 0.0
        self.detected_language: Optional[str] = None

        self.created_at: float = time.time()
        self.completed_at: Optional[float] = None
        self.temp_files: List[Path] = []
        if source_path:
            self.temp_files.append(source_path)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "source_type": self.source_type,
            "filename": self.filename,
            "status": self.status,
            "progress": self.progress,
            "stage": self.stage,
            "error": self.error,
            "text": self.text,
            "segments": self.segments,
            "duration": self.duration,
            "language": self.detected_language or self.language or "auto",
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


class TranscriptionManager:
    def __init__(self):
        self._jobs: Dict[str, TranscriptionJob] = {}
        self._lock = asyncio.Lock()

    def get_job(self, job_id: str) -> Optional[TranscriptionJob]:
        return self._jobs.get(job_id)

    def cleanup_job_files(self, job: TranscriptionJob) -> None:
        """Immediately remove all temporary files associated with this job."""
        for path in job.temp_files:
            try:
                if path and path.exists():
                    if path.is_file():
                        path.unlink(missing_ok=True)
                    elif path.is_dir():
                        shutil.rmtree(path, ignore_errors=True)
                    logger.debug(f"Removed temp file: {path}")
            except Exception as e:
                logger.warning(f"Failed to remove temp file {path}: {e}")
        job.temp_files.clear()

    def cleanup_expired(self, max_age_seconds: int = 900) -> None:
        """Clean up finished jobs older than max_age_seconds (15 mins default)."""
        now = time.time()
        expired_ids = [
            jid for jid, j in self._jobs.items()
            if (now - j.created_at) > max_age_seconds
        ]
        for jid in expired_ids:
            job = self._jobs.pop(jid, None)
            if job:
                self.cleanup_job_files(job)
                logger.info(f"Cleaned up expired transcription job: {jid}")

    def create_file_job(
        self,
        source_path: Path,
        filename: str,
        language: Optional[str] = None,
        diarization: bool = False,
    ) -> TranscriptionJob:
        job_id = f"tx_{uuid.uuid4().hex[:12]}"
        job = TranscriptionJob(
            job_id=job_id,
            source_type="file",
            filename=filename,
            language=language,
            diarization=diarization,
            source_path=source_path,
        )
        self._jobs[job_id] = job
        asyncio.create_task(self._process_job(job))
        return job

    def create_url_job(
        self,
        url: str,
        language: Optional[str] = None,
        diarization: bool = False,
    ) -> TranscriptionJob:
        job_id = f"tx_{uuid.uuid4().hex[:12]}"
        filename = sanitize_filename(url.split("/")[-1].split("?")[0] or "media_url")
        job = TranscriptionJob(
            job_id=job_id,
            source_type="url",
            filename=filename,
            language=language,
            diarization=diarization,
            source_url=url,
        )
        self._jobs[job_id] = job
        asyncio.create_task(self._process_job(job))
        return job

    async def _download_url_audio(self, job: TranscriptionJob, temp_dir: Path) -> Path:
        """Download or stream audio from URL with yt-dlp, extracting only the audio."""
        url = job.source_url
        if not url:
            raise ValueError("No source URL provided.")

        audio_output = temp_dir / f"{job.job_id}_remote.%(ext)s"

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": str(audio_output),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 30,
            "max_filesize": settings.MAX_TRANSCRIPTION_FILE_SIZE_MB * 1024 * 1024,
            "extractor_args": {
                "youtube": {
                    "player_client": ["web_embedded", "android", "ios", "mweb", "web"],
                    "player_skip": ["configs"],
                }
            }
        }

        loop = asyncio.get_running_loop()

        def _run_extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                downloaded_file = ydl.prepare_filename(info)
                return Path(downloaded_file), info.get("title")

        downloaded_path, title = await loop.run_in_executor(None, _run_extract)
        if title:
            job.filename = sanitize_filename(title)
        job.temp_files.append(downloaded_path)
        return downloaded_path

    async def _process_job(self, job: TranscriptionJob) -> None:
        temp_dir = settings.temp_dir_path
        job.status = "processing"
        job.progress = 10

        try:
            # Step 1: Ingest source audio
            if job.source_type == "url":
                job.stage = "downloading_media"
                job.progress = 20
                logger.info(f"Downloading remote media for transcription job {job.job_id}...")
                input_file = await self._download_url_audio(job, temp_dir)
            else:
                input_file = job.source_path
                if not input_file or not input_file.exists():
                    raise FileNotFoundError("Uploaded audio file is missing or unreadable.")

            # Step 2: Audio extraction and compression
            job.stage = "extracting_audio"
            job.progress = 40
            logger.info(f"Extracting and compressing audio for job {job.job_id}...")

            compressed_audio_path = temp_dir / f"{job.job_id}_compressed.mp3"
            loop = asyncio.get_running_loop()
            final_audio_path = await loop.run_in_executor(
                None,
                extract_compressed_audio,
                input_file,
                compressed_audio_path
            )
            if final_audio_path != input_file:
                job.temp_files.append(final_audio_path)

            # Check file size limit
            if final_audio_path.stat().st_size > (settings.MAX_TRANSCRIPTION_FILE_SIZE_MB * 1024 * 1024):
                raise ValueError(
                    f"File size exceeds the limit of {settings.MAX_TRANSCRIPTION_FILE_SIZE_MB} MB."
                )

            # Step 3: Call external AI Speech API
            job.stage = "transcribing"
            job.progress = 65
            logger.info(f"Calling external transcription service for job {job.job_id}...")

            result = await execute_transcription(final_audio_path, language=job.language)

            # Step 4: Finalize results
            job.text = result.get("text", "")
            job.segments = result.get("segments", [])
            job.duration = result.get("duration", 0.0)
            job.detected_language = result.get("language", "auto")

            job.stage = "completed"
            job.status = "completed"
            job.progress = 100
            job.completed_at = time.time()
            logger.info(f"Transcription job {job.job_id} completed successfully ({len(job.segments)} segments).")

        except Exception as e:
            logger.error(f"Transcription job {job.job_id} failed: {e}", exc_info=True)
            job.status = "failed"
            job.stage = "failed"
            job.error = str(e)
            job.completed_at = time.time()
        finally:
            # Immediate cleanup of temporary media files to preserve Botkeep 2GB disk limit
            self.cleanup_job_files(job)


transcription_manager = TranscriptionManager()
