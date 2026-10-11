import asyncio
import glob
import logging
import mimetypes
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, Optional, Any
import yt_dlp
from app.config import settings
from app.security import sanitize_filename
from app.services.ytdlp_service import check_ffmpeg_installed

logger = logging.getLogger("video_downloader.job_manager")


class DownloadJob:
    def __init__(self, job_id: str, url: str, format_id: str):
        self.job_id: str = job_id
        self.url: str = url
        self.format_id: str = format_id
        self.status: str = "queued"  # queued, processing, completed, failed
        self.progress_percent: float = 0.0
        self.downloaded_bytes: int = 0
        self.total_bytes: Optional[int] = None
        self.speed_bytes_sec: Optional[float] = None
        self.eta_seconds: Optional[int] = None
        self.file_path: Optional[str] = None
        self.filename: Optional[str] = None
        self.file_size: Optional[int] = None
        self.content_type: Optional[str] = None
        self.error_message: Optional[str] = None
        self.created_at: float = time.time()
        self.completed_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "url": self.url,
            "format_id": self.format_id,
            "status": self.status,
            "progress_percent": round(self.progress_percent, 1),
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "speed_bytes_sec": self.speed_bytes_sec,
            "eta_seconds": self.eta_seconds,
            "filename": self.filename,
            "file_size": self.file_size,
            "content_type": self.content_type,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


class JobManager:
    def __init__(self):
        self._jobs: Dict[str, DownloadJob] = {}
        self._lock = threading.Lock()
        self._semaphore = threading.Semaphore(settings.MAX_CONCURRENT_DOWNLOADS)

    def create_job(self, url: str, format_id: str) -> DownloadJob:
        with self._lock:
            job_id = str(uuid.uuid4())
            job = DownloadJob(job_id=job_id, url=url, format_id=format_id)
            self._jobs[job_id] = job
            return job

    def get_job(self, job_id: str) -> Optional[DownloadJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def delete_job(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.pop(job_id, None)
            if not job:
                return False

        # Remove file if it exists
        if job.file_path and os.path.exists(job.file_path):
            try:
                os.remove(job.file_path)
            except Exception as e:
                logger.warning(f"Failed to delete file {job.file_path}: {e}")

        # Also remove any leftover part files with prefix
        temp_dir = settings.temp_dir_path
        for f in temp_dir.glob(f"{job_id}*"):
            try:
                if f.is_file():
                    f.unlink()
            except Exception:
                pass

        return True

    def cleanup_expired(self):
        """Remove jobs and files that have exceeded DOWNLOAD_TTL_SECONDS."""
        now = time.time()
        expired_ids = []

        with self._lock:
            for jid, job in self._jobs.items():
                if now - job.created_at > settings.DOWNLOAD_TTL_SECONDS:
                    expired_ids.append(jid)

        for jid in expired_ids:
            self.delete_job(jid)

        # Also purge any orphan files in temp_dir older than TTL
        try:
            temp_dir = settings.temp_dir_path
            for f in temp_dir.iterdir():
                if f.is_file():
                    mtime = f.stat().st_mtime
                    if now - mtime > settings.DOWNLOAD_TTL_SECONDS:
                        f.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Error during temp directory cleanup: {e}")

    def execute_download(self, job_id: str):
        """Worker function running in a separate thread."""
        job = self.get_job(job_id)
        if not job:
            return

        with self._semaphore:
            job.status = "processing"
            ffmpeg_available = check_ffmpeg_installed()
            temp_dir = settings.temp_dir_path

            # Format logic
            format_id = job.format_id
            ydl_format = "best"
            postprocessors = []

            if format_id.startswith("video_"):
                res = format_id.replace("video_", "").replace("p", "")
                if res.isdigit():
                    h = int(res)
                    if ffmpeg_available:
                        ydl_format = f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"
                    else:
                        ydl_format = f"best[height<={h}][ext=mp4]/best[height<={h}]/best"
            elif format_id == "best_video":
                if ffmpeg_available:
                    ydl_format = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"
                else:
                    ydl_format = "best[ext=mp4]/best"
            elif format_id == "audio_mp3":
                ydl_format = "bestaudio/best"
                if ffmpeg_available:
                    postprocessors.append({
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    })
            elif format_id == "audio_m4a":
                ydl_format = "bestaudio[ext=m4a]/bestaudio/best"

            max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024

            def progress_hook(d: Dict[str, Any]):
                if d.get("status") == "downloading":
                    downloaded = d.get("downloaded_bytes", 0)
                    total = d.get("total_bytes") or d.get("total_bytes_estimate")
                    speed = d.get("speed")
                    eta = d.get("eta")

                    # Size check
                    if downloaded > max_bytes or (total and total > max_bytes):
                        raise Exception(f"File size exceeds the server limit of {settings.MAX_FILE_SIZE_MB}MB.")

                    job.downloaded_bytes = downloaded
                    job.total_bytes = total
                    job.speed_bytes_sec = speed
                    job.eta_seconds = eta

                    if total and total > 0:
                        job.progress_percent = min(99.0, (downloaded / total) * 100.0)
                    else:
                        job.progress_percent = 50.0  # Streaming / uncalculated total

                elif d.get("status") == "finished":
                    job.progress_percent = 99.0

            out_template = str(temp_dir / f"{job_id}_%(title).100s.%(ext)s")

            ydl_opts: Dict[str, Any] = {
                "format": ydl_format,
                "outtmpl": out_template,
                "progress_hooks": [progress_hook],
                "quiet": True,
                "no_warnings": True,
                "socket_timeout": 30,
                "max_filesize": max_bytes,
            }

            if postprocessors:
                ydl_opts["postprocessors"] = postprocessors

            if not ffmpeg_available:
                # Tell yt-dlp not to fail looking for ffmpeg if not installed
                ydl_opts["prefer_ffmpeg"] = False

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(job.url, download=True)

                # Locate the downloaded file
                pattern = str(temp_dir / f"{job_id}_*")
                matching_files = [f for f in glob.glob(pattern) if not f.endswith((".part", ".ytdl"))]

                if not matching_files:
                    raise Exception("Download completed but output media file could not be located.")

                final_path = matching_files[0]
                job.file_path = final_path
                job.file_size = os.path.getsize(final_path)

                # Extract a clean, safe filename for download attachment
                base_name = os.path.basename(final_path)
                # Strip job_id prefix
                if base_name.startswith(f"{job_id}_"):
                    clean_name = base_name[len(job_id) + 1:]
                else:
                    clean_name = base_name

                job.filename = sanitize_filename(clean_name, default="downloaded_media")
                mime_type, _ = mimetypes.guess_type(final_path)
                job.content_type = mime_type or "application/octet-stream"

                job.progress_percent = 100.0
                job.status = "completed"
                job.completed_at = time.time()

            except Exception as e:
                err_text = str(e)
                logger.error(f"Download failed for job {job_id}: {err_text}")
                job.status = "failed"
                # Sanitize error message to avoid internal paths
                if "exceeds the server limit" in err_text:
                    job.error_message = err_text
                elif "Private video" in err_text:
                    job.error_message = "This media is private and cannot be downloaded."
                elif "Sign in" in err_text or "login" in err_text.lower():
                    job.error_message = "This media requires login authentication."
                elif "HTTP Error 403" in err_text:
                    job.error_message = "Access to this media stream was forbidden by the hosting platform."
                elif "FFmpeg" in err_text and not ffmpeg_available:
                    job.error_message = "This format requires FFmpeg conversion, which is currently unavailable."
                else:
                    job.error_message = "Failed to download media. The content may be protected, geo-restricted, or unavailable."


job_manager = JobManager()
