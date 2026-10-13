import asyncio
import glob
import logging
import mimetypes
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, Optional, Any
import requests
import yt_dlp
from app.config import settings
from app.security import sanitize_filename, detect_media_platform, resolve_shortlink_url
from app.services.ytdlp_service import (
    check_ffmpeg_installed,
    extract_pinterest_metadata,
    extract_tiktok_metadata,
    extract_facebook_direct_metadata,
)

logger = logging.getLogger("video_downloader.job_manager")


class DownloadJob:
    def __init__(self, job_id: str, url: str, format_id: str):
        self.job_id: str = job_id
        self.url: str = url
        self.platform: str = detect_media_platform(url)
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
            "platform": self.platform,
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
            logger.info(f"Created job {job_id} for platform '{job.platform}' (format: {format_id})")
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
                logger.info(f"Removed media file for job {job_id}: {job.file_path}")
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

        start_time = time.time()
        logger.info(f"Starting download execution for job {job_id} [{job.platform}] format='{job.format_id}'")

        with self._semaphore:
            job.status = "processing"
            ffmpeg_available = check_ffmpeg_installed()
            temp_dir = settings.temp_dir_path
            max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024

            # --- DEDICATED PINTEREST DOWNLOAD HANDLER ---
            if job.platform == "Pinterest":
                try:
                    p_data = extract_pinterest_metadata(job.url)
                    direct_url = p_data.get("direct_media_url")
                    if not direct_url:
                        raise Exception("No direct media stream found for this Pinterest video.")

                    title_sanitized = sanitize_filename(p_data.get("title", "pinterest_video"))
                    final_mp4 = temp_dir / f"{job_id}_{title_sanitized}.mp4"

                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                        "Accept": "*/*",
                    }

                    r = requests.get(direct_url, headers=headers, stream=True, timeout=30)
                    r.raise_for_status()

                    total_len = int(r.headers.get("content-length", 0))
                    job.total_bytes = total_len if total_len > 0 else None
                    downloaded = 0

                    with open(final_mp4, "wb") as f:
                        for chunk in r.iter_content(chunk_size=65536):
                            if not chunk:
                                continue
                            f.write(chunk)
                            downloaded += len(chunk)
                            job.downloaded_bytes = downloaded
                            if downloaded > max_bytes:
                                raise Exception(f"File size exceeds the server limit of {settings.MAX_FILE_SIZE_MB}MB.")
                            if total_len > 0:
                                job.progress_percent = min(99.0, (downloaded / total_len) * 100.0)

                    # Audio conversion if requested
                    if job.format_id == "audio_mp3":
                        if not ffmpeg_available:
                            raise Exception("MP3 audio extraction requires FFmpeg, which is not available.")
                        mp3_path = temp_dir / f"{job_id}_{title_sanitized}.mp3"
                        cmd = ["ffmpeg", "-y", "-i", str(final_mp4), "-vn", "-c:a", "libmp3lame", "-b:a", "320k", str(mp3_path)]
                        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
                        if res.returncode != 0:
                            raise Exception(f"FFmpeg audio extraction failed: {res.stderr.decode('utf-8', errors='ignore')}")
                        if os.path.exists(final_mp4):
                            os.remove(final_mp4)
                        output_path = mp3_path
                        job.content_type = "audio/mpeg"
                    else:
                        output_path = final_mp4
                        job.content_type = "video/mp4"

                    job.file_path = str(output_path)
                    job.file_size = os.path.getsize(output_path)
                    job.filename = sanitize_filename(output_path.name[len(job_id) + 1:])
                    job.progress_percent = 100.0
                    job.status = "completed"
                    job.completed_at = time.time()
                    elapsed = round(job.completed_at - start_time, 2)
                    logger.info(f"Download SUCCESS for Pinterest job {job_id}: {job.file_size} bytes in {elapsed}s")
                    return

                except Exception as e:
                    err_text = str(e)
                    elapsed = round(time.time() - start_time, 2)
                    logger.error(f"Download FAILED for Pinterest job {job_id} after {elapsed}s: {err_text}")
                    job.status = "failed"
                    job.error_message = err_text if "exceeds the server limit" in err_text else "Failed to download Pinterest video."
                    return

            # --- DEDICATED TIKTOK STREAMING DOWNLOAD HANDLER ---
            if job.platform == "TikTok":
                try:
                    tt_data = extract_tiktok_metadata(job.url)
                    if tt_data:
                        direct_url = None
                        is_audio = job.format_id == "audio_mp3"
                        for f in tt_data.get("formats", []):
                            if f.get("format_id") == job.format_id and f.get("direct_url"):
                                direct_url = f.get("direct_url")
                                break
                        if not direct_url:
                            direct_url = tt_data.get("direct_media_url")

                        if direct_url:
                            title_sanitized = sanitize_filename(tt_data.get("title", "tiktok_video"))
                            ext = "mp3" if is_audio else "mp4"
                            final_file = temp_dir / f"{job_id}_{title_sanitized}.{ext}"

                            headers = {
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                                "Accept": "*/*",
                            }

                            r = requests.get(direct_url, headers=headers, stream=True, timeout=30)
                            r.raise_for_status()

                            total_len = int(r.headers.get("content-length", 0))
                            job.total_bytes = total_len if total_len > 0 else None
                            downloaded = 0

                            with open(final_file, "wb") as f:
                                for chunk in r.iter_content(chunk_size=65536):
                                    if not chunk:
                                        continue
                                    f.write(chunk)
                                    downloaded += len(chunk)
                                    job.downloaded_bytes = downloaded
                                    if downloaded > max_bytes:
                                        raise Exception(f"File size exceeds the server limit of {settings.MAX_FILE_SIZE_MB}MB.")
                                    if total_len > 0:
                                        job.progress_percent = min(99.0, (downloaded / total_len) * 100.0)

                            job.file_path = str(final_file)
                            job.file_size = os.path.getsize(final_file)
                            job.filename = sanitize_filename(final_file.name[len(job_id) + 1:])
                            job.content_type = "audio/mpeg" if is_audio else "video/mp4"
                            job.progress_percent = 100.0
                            job.status = "completed"
                            job.completed_at = time.time()
                            elapsed = round(job.completed_at - start_time, 2)
                            logger.info(f"Download SUCCESS for TikTok job {job_id}: {job.file_size} bytes in {elapsed}s")
                            return
                except Exception as e:
                    logger.warning(f"TikTok streaming download failed for job {job_id}, attempting yt-dlp fallback: {e}")

            # --- STANDARD YT-DLP DOWNLOAD HANDLER (YouTube, TikTok, Facebook, Instagram) ---
            target_url = resolve_shortlink_url(job.url)
            format_id = job.format_id
            ydl_format = "best"
            postprocessors = []

            if job.platform == "Facebook":
                # Prefer progressive single-file MP4 for Facebook to eliminate CDN chunk drops
                if format_id.startswith("video_"):
                    res = format_id.replace("video_", "").replace("p", "")
                    h = int(res) if res.isdigit() else 720
                    ydl_format = f"best[height<={h}][ext=mp4]/bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/best[height<={h}]/best"
                elif format_id == "best_video":
                    ydl_format = "best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best"
                elif format_id == "audio_mp3":
                    ydl_format = "bestaudio/best"
                    if ffmpeg_available:
                        postprocessors.append({
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": "320",
                        })
            else:
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
                            "preferredquality": "320",
                        })
                elif format_id == "audio_m4a":
                    ydl_format = "bestaudio[ext=m4a]/bestaudio/best"

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
                        job.progress_percent = 50.0

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
                "retries": 10,
                "fragment_retries": 10,
                "file_access_retries": 5,
                "buffersize": 1024 * 1024,
                "continuedl": True,
                "nocheckcertificate": True,
                "max_filesize": max_bytes,
                "http_headers": {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                }
            }

            if postprocessors:
                ydl_opts["postprocessors"] = postprocessors

            if not ffmpeg_available:
                ydl_opts["prefer_ffmpeg"] = False

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.extract_info(target_url, download=True)

                # Locate the downloaded file
                pattern = str(temp_dir / f"{job_id}_*")
                matching_files = [f for f in glob.glob(pattern) if not f.endswith((".part", ".ytdl"))]

                if not matching_files:
                    raise Exception("Download completed but output media file could not be located.")

                final_path = matching_files[0]
                job.file_path = final_path
                job.file_size = os.path.getsize(final_path)

                base_name = os.path.basename(final_path)
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
                elapsed = round(job.completed_at - start_time, 2)
                logger.info(
                    f"Download SUCCESS for job {job_id} [{job.platform}]: "
                    f"size={job.file_size} bytes ({round(job.file_size / (1024*1024), 2)} MB) "
                    f"in {elapsed}s"
                )

            except Exception as e:
                err_text = str(e)
                logger.warning(f"Standard yt-dlp download failed for job {job_id} [{job.platform}]: {err_text}")

                # If Facebook yt-dlp download failed, attempt direct HTML scraper fallback
                if job.platform == "Facebook":
                    try:
                        logger.info(f"Attempting direct Facebook fallback download for job {job_id}...")
                        fb_data = extract_facebook_direct_metadata(job.url)
                        if fb_data and fb_data.get("direct_media_url"):
                            direct_url = fb_data["direct_media_url"]
                            title_sanitized = sanitize_filename(fb_data.get("title", "facebook_video"))
                            final_mp4 = temp_dir / f"{job_id}_{title_sanitized}.mp4"

                            headers = {
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                                "Accept": "*/*",
                            }

                            r = requests.get(direct_url, headers=headers, stream=True, timeout=30)
                            r.raise_for_status()

                            total_len = int(r.headers.get("content-length", 0))
                            job.total_bytes = total_len if total_len > 0 else None
                            downloaded = 0

                            with open(final_mp4, "wb") as f:
                                for chunk in r.iter_content(chunk_size=65536):
                                    if not chunk:
                                        continue
                                    f.write(chunk)
                                    downloaded += len(chunk)
                                    job.downloaded_bytes = downloaded
                                    if downloaded > max_bytes:
                                        raise Exception(f"File size exceeds the server limit of {settings.MAX_FILE_SIZE_MB}MB.")
                                    if total_len > 0:
                                        job.progress_percent = min(99.0, (downloaded / total_len) * 100.0)

                            if job.format_id == "audio_mp3" and ffmpeg_available:
                                mp3_path = temp_dir / f"{job_id}_{title_sanitized}.mp3"
                                cmd = ["ffmpeg", "-y", "-i", str(final_mp4), "-vn", "-c:a", "libmp3lame", "-b:a", "320k", str(mp3_path)]
                                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
                                if res.returncode == 0:
                                    if os.path.exists(final_mp4):
                                        os.remove(final_mp4)
                                    output_path = mp3_path
                                    job.content_type = "audio/mpeg"
                                else:
                                    output_path = final_mp4
                                    job.content_type = "video/mp4"
                            else:
                                output_path = final_mp4
                                job.content_type = "video/mp4"

                            job.file_path = str(output_path)
                            job.file_size = os.path.getsize(output_path)
                            job.filename = sanitize_filename(output_path.name[len(job_id) + 1:])
                            job.progress_percent = 100.0
                            job.status = "completed"
                            job.completed_at = time.time()
                            elapsed = round(job.completed_at - start_time, 2)
                            logger.info(f"Download SUCCESS for Facebook direct fallback job {job_id}: {job.file_size} bytes in {elapsed}s")
                            return
                    except Exception as fb_err:
                        logger.warning(f"Facebook direct fallback also failed for job {job_id}: {fb_err}")

                elapsed = round(time.time() - start_time, 2)
                logger.error(f"Download FAILED for job {job_id} [{job.platform}] after {elapsed}s: {err_text}")
                job.status = "failed"

                # Cleanup any partial files for this failed job
                for f in temp_dir.glob(f"{job_id}*"):
                    try:
                        if f.is_file():
                            f.unlink()
                    except Exception:
                        pass

                # Structured platform-specific error messages
                if "exceeds the server limit" in err_text:
                    job.error_message = err_text
                elif "Private video" in err_text or "this video is private" in err_text.lower() or "not available" in err_text.lower():
                    job.error_message = f"This {job.platform} media is private or restricted by creator permissions."
                elif "Sign in" in err_text or "login" in err_text.lower():
                    job.error_message = f"This {job.platform} media requires account login."
                elif "HTTP Error 403" in err_text:
                    job.error_message = f"Access to this {job.platform} media stream was restricted by the host."
                elif "FFmpeg" in err_text and not ffmpeg_available:
                    job.error_message = "This format requires FFmpeg conversion, which is currently unavailable."
                elif "Unsupported URL" in err_text:
                    job.error_message = f"This {job.platform} URL format is unsupported."
                else:
                    job.error_message = f"Failed to download {job.platform} media. Please try a different quality or check if the video is publicly visible."


job_manager = JobManager()
