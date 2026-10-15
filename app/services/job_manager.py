import asyncio
import glob
import logging
import mimetypes
import os
import re
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
from app.services.cobalt_service import extract_cobalt_media
from app.services.ytdlp_service import (
    check_ffmpeg_installed,
    extract_pinterest_metadata,
    extract_tiktok_metadata,
    extract_facebook_direct_metadata,
    find_cookies_file,
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


def stream_direct_media_to_file(
    job: DownloadJob,
    direct_url: str,
    title: str,
    is_audio: bool,
    ffmpeg_available: bool,
    start_time: float,
    temp_dir: Path,
    max_bytes: int,
) -> bool:
    """
    Download a direct progressive media stream in chunks, updating job progress in real time.
    Transcodes to 320kbps MP3 if audio is requested and FFmpeg is present.
    Saves image streams as JPG when image format is requested.
    """
    title_sanitized = sanitize_filename(title, default=f"{job.platform.lower()}_media")
    temp_part = temp_dir / f"{job.job_id}_{title_sanitized}.part"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "*/*",
    }

    try:
        r = requests.get(direct_url, headers=headers, stream=True, timeout=35)
        r.raise_for_status()

        total_len = int(r.headers.get("content-length", 0))
        job.total_bytes = total_len if total_len > 0 else None
        downloaded = 0

        with open(temp_part, "wb") as f:
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

        is_image = (
            job.format_id.startswith("image")
            or direct_url.lower().split("?")[0].endswith((".jpg", ".jpeg", ".png", ".webp"))
            or r.headers.get("content-type", "").startswith("image/")
        )

        if is_image:
            final_img = temp_dir / f"{job.job_id}_{title_sanitized}.jpg"
            temp_part.replace(final_img)
            final_path = final_img
            job.content_type = "image/jpeg"
        elif is_audio:
            if not ffmpeg_available:
                if direct_url.endswith((".mp3", ".m4a", ".aac")) or "audio" in r.headers.get("content-type", ""):
                    final_path = temp_dir / f"{job.job_id}_{title_sanitized}.mp3"
                    temp_part.replace(final_path)
                    job.content_type = "audio/mpeg"
                else:
                    raise Exception("MP3 audio extraction requires FFmpeg, which is not available.")
            else:
                mp3_path = temp_dir / f"{job.job_id}_{title_sanitized}.mp3"
                cmd = ["ffmpeg", "-y", "-i", str(temp_part), "-vn", "-c:a", "libmp3lame", "-b:a", "320k", str(mp3_path)]
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
                if res.returncode != 0:
                    raise Exception(f"FFmpeg audio extraction failed: {res.stderr.decode('utf-8', errors='ignore')}")
                if temp_part.exists():
                    temp_part.unlink(missing_ok=True)
                final_path = mp3_path
                job.content_type = "audio/mpeg"
        else:
            final_mp4 = temp_dir / f"{job.job_id}_{title_sanitized}.mp4"
            temp_part.replace(final_mp4)
            final_path = final_mp4
            job.content_type = "video/mp4"

        job.file_path = str(final_path)
        job.file_size = os.path.getsize(final_path)
        job.filename = sanitize_filename(final_path.name[len(job.job_id) + 1:])
        job.progress_percent = 100.0
        job.status = "completed"
        job.completed_at = time.time()
        elapsed = round(job.completed_at - start_time, 2)
        logger.info(f"Download SUCCESS for direct stream job {job.job_id} [{job.platform}]: {job.file_size} bytes in {elapsed}s")
        return True

    except Exception as e:
        logger.warning(f"Direct stream download failed for {job.job_id} [{job.platform}]: {e}")
        if temp_part.exists():
            temp_part.unlink(missing_ok=True)
        return False


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
                    if p_data and p_data.get("direct_media_url"):
                        is_audio = job.format_id == "audio_mp3"
                        if stream_direct_media_to_file(job, p_data["direct_media_url"], p_data.get("title", "pinterest_video"), is_audio, ffmpeg_available, start_time, temp_dir, max_bytes):
                            return
                except Exception as e:
                    logger.warning(f"Pinterest streaming failed for {job.job_id}: {e}")

            # --- DEDICATED TIKTOK DOWNLOAD HANDLER (TikWM + Cobalt) ---
            if job.platform == "TikTok":
                is_audio = job.format_id == "audio_mp3"
                # Tier 1: TikWM Direct Stream
                try:
                    tt_data = extract_tiktok_metadata(job.url)
                    if tt_data:
                        direct_url = None
                        for f in tt_data.get("formats", []):
                            if f.get("format_id") == job.format_id and f.get("direct_url"):
                                direct_url = f.get("direct_url")
                                break
                        if not direct_url:
                            direct_url = tt_data.get("direct_media_url")
                        if direct_url and stream_direct_media_to_file(job, direct_url, tt_data.get("title", "tiktok_video"), is_audio, ffmpeg_available, start_time, temp_dir, max_bytes):
                            return
                except Exception as e:
                    logger.warning(f"TikTok TikWM direct stream failed for {job.job_id}: {e}")

                # Tier 2: Cobalt Engine (no watermark high speed)
                try:
                    cobalt_data = extract_cobalt_media(job.url)
                    if cobalt_data and cobalt_data.get("direct_media_url"):
                        if stream_direct_media_to_file(job, cobalt_data["direct_media_url"], cobalt_data.get("title", "tiktok_video"), is_audio, ffmpeg_available, start_time, temp_dir, max_bytes):
                            return
                except Exception as e:
                    logger.warning(f"TikTok Cobalt direct stream failed for {job.job_id}: {e}")

            # --- DEDICATED FACEBOOK DOWNLOAD HANDLER (Cobalt + Direct Scraper) ---
            if job.platform == "Facebook":
                is_audio = job.format_id == "audio_mp3"
                # Tier 1: Cobalt Engine (1080p/720p direct stream)
                try:
                    fb_target = job.url
                    reel_m = re.search(r'facebook\.com/reel/(\d+)', job.url)
                    if reel_m:
                        fb_target = f"https://www.facebook.com/watch/?v={reel_m.group(1)}"
                    cobalt_data = extract_cobalt_media(fb_target) or extract_cobalt_media(job.url)
                    if cobalt_data and cobalt_data.get("direct_media_url"):
                        if stream_direct_media_to_file(job, cobalt_data["direct_media_url"], cobalt_data.get("title", "facebook_video"), is_audio, ffmpeg_available, start_time, temp_dir, max_bytes):
                            return
                except Exception as e:
                    logger.warning(f"Facebook Cobalt stream download failed for {job.job_id}: {e}")

                # Tier 2: Direct Facebook HTML Scraper
                try:
                    fb_data = extract_facebook_direct_metadata(job.url)
                    if fb_data and fb_data.get("direct_media_url"):
                        if stream_direct_media_to_file(job, fb_data["direct_media_url"], fb_data.get("title", "facebook_video"), is_audio, ffmpeg_available, start_time, temp_dir, max_bytes):
                            return
                except Exception as e:
                    logger.warning(f"Facebook direct scraper download failed for {job.job_id}: {e}")

            # --- DEDICATED INSTAGRAM DOWNLOAD HANDLER (Cobalt + Direct Photo Stream) ---
            if job.platform == "Instagram":
                is_audio = job.format_id == "audio_mp3"
                # Tier 1: Cobalt Direct Stream (photos/reels)
                try:
                    cobalt_data = extract_cobalt_media(job.url)
                    if cobalt_data and cobalt_data.get("direct_media_url"):
                        if stream_direct_media_to_file(job, cobalt_data["direct_media_url"], cobalt_data.get("title", "instagram_post"), is_audio, ffmpeg_available, start_time, temp_dir, max_bytes):
                            return
                except Exception as cob_err:
                    logger.warning(f"Instagram Cobalt direct stream failed for {job.job_id}: {cob_err}")

                # Tier 2: If requesting an image format, try yt-dlp metadata extraction to get direct photo URL
                if job.format_id.startswith("image"):
                    try:
                        cookie_file = find_cookies_file()
                        ydl_img_opts = {
                            "skip_download": True,
                            "quiet": True,
                            "no_warnings": True,
                        }
                        if cookie_file:
                            ydl_img_opts["cookiefile"] = cookie_file
                        with yt_dlp.YoutubeDL(ydl_img_opts) as ydl:
                            img_info = ydl.extract_info(job.url, download=False)
                            if "entries" in img_info and img_info["entries"]:
                                img_info = img_info["entries"][0]
                            img_url = img_info.get("url") or img_info.get("thumbnail")
                            if img_url:
                                if stream_direct_media_to_file(job, img_url, img_info.get("title", "instagram_photo"), False, ffmpeg_available, start_time, temp_dir, max_bytes):
                                    return
                    except Exception as img_err:
                        logger.warning(f"Instagram photo direct stream failed for {job.job_id}: {img_err}")

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
                elif format_id.startswith("image"):
                    ydl_format = "best/all"

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

            cookie_file = find_cookies_file()
            if cookie_file:
                ydl_opts["cookiefile"] = cookie_file
                logger.info(f"Job {job_id} using cookies file: {cookie_file}")

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

                if final_path.lower().endswith((".jpg", ".jpeg")):
                    job.content_type = "image/jpeg"
                elif final_path.lower().endswith(".png"):
                    job.content_type = "image/png"
                elif final_path.lower().endswith(".webp"):
                    job.content_type = "image/webp"
                else:
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

                # Universal Fallback: Cobalt Engine if yt-dlp failed during download
                try:
                    cobalt_fallback = extract_cobalt_media(job.url)
                    if cobalt_fallback and cobalt_fallback.get("direct_media_url"):
                        is_audio = job.format_id == "audio_mp3"
                        if stream_direct_media_to_file(job, cobalt_fallback["direct_media_url"], cobalt_fallback.get("title", "media"), is_audio, ffmpeg_available, start_time, temp_dir, max_bytes):
                            return
                except Exception as cob_err:
                    logger.warning(f"Cobalt universal download fallback failed for {job_id}: {cob_err}")

                # If Facebook yt-dlp download failed, attempt direct HTML scraper fallback
                if job.platform == "Facebook":
                    try:
                        logger.info(f"Attempting direct Facebook fallback download for job {job_id}...")
                        fb_data = extract_facebook_direct_metadata(job.url)
                        if fb_data and fb_data.get("direct_media_url"):
                            is_audio = job.format_id == "audio_mp3"
                            if stream_direct_media_to_file(job, fb_data["direct_media_url"], fb_data.get("title", "facebook_video"), is_audio, ffmpeg_available, start_time, temp_dir, max_bytes):
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
                elif "empty media response" in err_text.lower() or "login_required" in err_text.lower():
                    job.error_message = "Instagram requires account authentication to download this post. Please ensure the post is public, or supply a cookies.txt file."
                elif "FFmpeg" in err_text and not ffmpeg_available:
                    job.error_message = "This format requires FFmpeg conversion, which is currently unavailable."
                elif "Unsupported URL" in err_text:
                    job.error_message = f"This {job.platform} URL format is unsupported."
                else:
                    job.error_message = f"Failed to download {job.platform} media. Please try a different quality or check if the video is publicly visible."


job_manager = JobManager()
