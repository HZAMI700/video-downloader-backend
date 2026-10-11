import logging
import shutil
import subprocess
from typing import Dict, Any, List, Optional
import yt_dlp
from fastapi import HTTPException
from app.config import settings
from app.security import detect_media_platform

logger = logging.getLogger("video_downloader.ytdlp")

_FFMPEG_AVAILABLE: Optional[bool] = None


def check_ffmpeg_installed() -> bool:
    """Check if the FFmpeg executable is actually available in system PATH."""
    global _FFMPEG_AVAILABLE
    if _FFMPEG_AVAILABLE is not None:
        return _FFMPEG_AVAILABLE

    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        _FFMPEG_AVAILABLE = False
        return False

    try:
        res = subprocess.run(
            [ffmpeg_path, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3,
            check=False
        )
        _FFMPEG_AVAILABLE = (res.returncode == 0)
    except Exception:
        _FFMPEG_AVAILABLE = False

    return _FFMPEG_AVAILABLE


def format_duration(seconds: Optional[int]) -> Optional[str]:
    """Format duration in seconds to HH:MM:SS or MM:SS."""
    if seconds is None or seconds < 0:
        return None
    hours = int(seconds) // 3600
    minutes = (int(seconds) % 3600) // 60
    secs = int(seconds) % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def estimate_size_bytes(duration_sec: Optional[float], height: int) -> int:
    """Estimate media file size if yt-dlp doesn't supply it."""
    dur = duration_sec or 180.0  # default 3 mins
    # Approximate bitrates: 1080p ~ 4Mbps, 720p ~ 2Mbps, 480p ~ 1Mbps, 360p ~ 600Kbps, audio ~ 320Kbps
    if height >= 1080:
        bitrate_kbps = 4500
    elif height >= 720:
        bitrate_kbps = 2500
    elif height >= 480:
        bitrate_kbps = 1200
    elif height >= 360:
        bitrate_kbps = 800
    elif height == 0:  # Audio
        bitrate_kbps = 320
    else:
        bitrate_kbps = 1500

    return int((bitrate_kbps * 1000 / 8) * dur)


def extract_media_metadata(url: str) -> Dict[str, Any]:
    """
    Extract video metadata using yt-dlp supporting YouTube, TikTok, Facebook, Instagram, etc.
    Returns structured details and supported formats.
    """
    ffmpeg_available = check_ffmpeg_installed()
    detected_platform = detect_media_platform(url)

    ydl_opts: Dict[str, Any] = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "socket_timeout": 20,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        logger.warning(f"Metadata extraction DownloadError for {detected_platform} URL: {msg}")
        if "Private video" in msg or "this video is private" in msg.lower():
            raise HTTPException(status_code=403, detail="This video is private and cannot be accessed.")
        if "Sign in" in msg or "login" in msg.lower():
            raise HTTPException(status_code=403, detail="This media requires account login and cannot be accessed.")
        if "Unsupported URL" in msg:
            raise HTTPException(status_code=400, detail="The provided URL is not supported.")
        raise HTTPException(status_code=400, detail="Failed to retrieve media. The media may be unavailable, restricted, or removed.")
    except Exception as e:
        logger.error(f"Unexpected error extracting metadata for {url}: {e}")
        raise HTTPException(status_code=500, detail=f"Unexpected error while extracting metadata: {str(e)}")

    if not info:
        raise HTTPException(status_code=404, detail="No media information found for the requested URL.")

    # Process formats
    raw_formats = info.get("formats", [])
    duration_sec = info.get("duration")

    processed_formats: List[Dict[str, Any]] = []
    seen_resolutions = set()

    # Collect available video resolutions
    video_map: Dict[str, Dict[str, Any]] = {}
    best_single_format: Optional[Dict[str, Any]] = None

    for f in raw_formats:
        vcodec = f.get("vcodec", "none")
        acodec = f.get("acodec", "none")
        height = f.get("height")
        f_id = str(f.get("format_id", ""))
        filesize = f.get("filesize") or f.get("filesize_approx")

        is_video = vcodec != "none"
        is_audio = acodec != "none"
        is_direct = is_video and is_audio

        # Special handling for Facebook 'hd' and 'sd'
        if "hd" in f_id.lower() and not height:
            height = 1080
            is_video = True
        elif "sd" in f_id.lower() and not height:
            height = 480
            is_video = True

        if is_video and height:
            res_key = f"{height}p"
            if res_key not in video_map or (is_direct and not video_map[res_key].get("is_direct")):
                video_map[res_key] = {
                    "height": height,
                    "is_direct": is_direct,
                    "filesize": filesize or estimate_size_bytes(duration_sec, height),
                }

        if is_direct:
            if not best_single_format or (height and height > (best_single_format.get("height") or 0)):
                best_single_format = f

    # Standard desired qualities: 1080p, 720p, 480p, 360p
    target_resolutions = ["1080p", "720p", "480p", "360p"]

    for res in target_resolutions:
        h = int(res.replace("p", ""))
        # Check if platform has this resolution or higher (downsampling is supported if ffmpeg exists)
        available_matching = [k for k, v in video_map.items() if v["height"] >= h]
        exact_match = res in video_map

        if exact_match or (ffmpeg_available and available_matching):
            vf = video_map.get(res) or video_map[available_matching[0]]
            requires_ffmpeg = not vf.get("is_direct", False)

            # Determine file size
            est_size = vf.get("filesize") or estimate_size_bytes(duration_sec, h)

            processed_formats.append({
                "format_id": f"video_{res}",
                "type": "video",
                "label": f"MP4 {res}",
                "resolution": res,
                "ext": "mp4",
                "quality_note": "Full High Definition" if h >= 1080 else "High Definition" if h >= 720 else "Standard Definition",
                "requires_ffmpeg": requires_ffmpeg,
                "available": ffmpeg_available or vf.get("is_direct", True),
                "approx_size_bytes": est_size,
            })
            seen_resolutions.add(res)

    # If no standard resolutions matched (common for TikTok or direct video links)
    if not processed_formats:
        est_size = (best_single_format.get("filesize") if best_single_format else None) or estimate_size_bytes(duration_sec, 720)
        processed_formats.append({
            "format_id": "best_video",
            "type": "video",
            "label": "MP4 (Best Quality)",
            "resolution": "HD",
            "ext": "mp4",
            "quality_note": "Original Platform Quality",
            "requires_ffmpeg": False,
            "available": True,
            "approx_size_bytes": est_size,
        })

    # Audio option: MP3 320kbps
    mp3_size = estimate_size_bytes(duration_sec, 0)
    processed_formats.append({
        "format_id": "audio_mp3",
        "type": "audio",
        "label": "MP3 (320kbps)",
        "resolution": "320kbps",
        "ext": "mp3",
        "quality_note": "High Bitrate Crystal Audio",
        "requires_ffmpeg": True,
        "available": True,
        "approx_size_bytes": mp3_size,
    })

    platform_title = detected_platform if detected_platform != "Web Media" else (info.get("extractor_key") or "Web Media")

    return {
        "id": str(info.get("id", "")),
        "title": info.get("title") or "Untitled Media",
        "thumbnail": info.get("thumbnail"),
        "duration": duration_sec,
        "duration_formatted": format_duration(duration_sec),
        "uploader": info.get("uploader") or info.get("channel") or info.get("creator") or "Unknown Creator",
        "view_count": info.get("view_count"),
        "platform": platform_title,
        "original_url": url,
        "formats": processed_formats,
        "ffmpeg_available": ffmpeg_available,
    }
