import shutil
import subprocess
from typing import Dict, Any, List, Optional
import yt_dlp
from fastapi import HTTPException
from app.config import settings


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
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def get_platform_name(extractor: Optional[str], webpage_url: str) -> str:
    """Identify readable platform name from extractor string or URL."""
    ext = (extractor or "").lower()
    url = (webpage_url or "").lower()

    if "youtube" in ext or "youtu.be" in url or "youtube.com" in url:
        return "YouTube"
    if "tiktok" in ext or "tiktok.com" in url:
        return "TikTok"
    if "instagram" in ext or "instagram.com" in url:
        return "Instagram"
    if "twitter" in ext or "x.com" in url or "twitter.com" in url:
        return "X (Twitter)"
    if "facebook" in ext or "facebook.com" in url or "fb.watch" in url:
        return "Facebook"
    if "reddit" in ext or "reddit.com" in url:
        return "Reddit"
    if "vimeo" in ext or "vimeo.com" in url:
        return "Vimeo"
    if "soundcloud" in ext or "soundcloud.com" in url:
        return "SoundCloud"
    if "twitch" in ext or "twitch.tv" in url:
        return "Twitch"
    return extractor.capitalize() if extractor else "Web Media"


def extract_media_metadata(url: str) -> Dict[str, Any]:
    """
    Extract video metadata using yt-dlp without downloading media content.
    Returns structured details and supported formats.
    """
    ffmpeg_available = check_ffmpeg_installed()

    ydl_opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "socket_timeout": 20,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if "Private video" in msg:
            raise HTTPException(status_code=403, detail="This video is private and cannot be accessed.")
        if "Sign in" in msg or "login" in msg.lower():
            raise HTTPException(status_code=403, detail="This media requires authentication and cannot be accessed.")
        if "Unsupported URL" in msg:
            raise HTTPException(status_code=400, detail="The provided URL is not supported by yt-dlp.")
        raise HTTPException(status_code=400, detail="Failed to retrieve media. The media may be unavailable, restricted, or removed.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error while extracting metadata: {str(e)}")

    if not info:
        raise HTTPException(status_code=404, detail="No media information found for the requested URL.")

    # Process formats
    raw_formats = info.get("formats", [])
    processed_formats: List[Dict[str, Any]] = []

    # Map resolutions to best available format
    # Standard targets: 1080p, 720p, 480p, 360p, best, audio_mp3
    video_resolutions: Dict[str, Dict[str, Any]] = {}
    best_direct_video: Optional[Dict[str, Any]] = None

    for f in raw_formats:
        vcodec = f.get("vcodec", "none")
        acodec = f.get("acodec", "none")
        height = f.get("height")
        ext = f.get("ext", "mp4")
        filesize = f.get("filesize") or f.get("filesize_approx")

        is_video = vcodec != "none"
        is_audio = acodec != "none"
        is_direct = is_video and is_audio

        if is_video and height:
            res_key = f"{height}p"
            # Prioritize mp4 extension or pre-merged format
            if res_key not in video_resolutions or (is_direct and not video_resolutions[res_key].get("is_direct")):
                video_resolutions[res_key] = {
                    "format_id": f.get("format_id"),
                    "height": height,
                    "ext": ext,
                    "filesize": filesize,
                    "is_direct": is_direct,
                    "resolution": res_key,
                }
            if is_direct:
                if not best_direct_video or (height > (best_direct_video.get("height") or 0)):
                    best_direct_video = f

    # Order common resolutions descending: 1080p, 720p, 480p, 360p
    target_res = ["1080p", "720p", "480p", "360p"]
    added_res = set()

    for res in target_res:
        if res in video_resolutions:
            vf = video_resolutions[res]
            requires_ffmpeg = not vf["is_direct"]
            is_usable = ffmpeg_available or vf["is_direct"]

            quality_note = "High Definition (MP4)" if res in ("1080p", "720p") else "Standard Definition (MP4)"
            if requires_ffmpeg and not ffmpeg_available:
                quality_note = "Requires FFmpeg (Not available on server)"

            processed_formats.append({
                "format_id": f"video_{res}",
                "type": "video",
                "label": f"{res} MP4",
                "resolution": res,
                "ext": "mp4",
                "quality_note": quality_note,
                "requires_ffmpeg": requires_ffmpeg,
                "available": is_usable,
                "approx_size_bytes": vf.get("filesize"),
            })
            added_res.add(res)

    # If no target resolutions matched, add best available video
    if not processed_formats:
        processed_formats.append({
            "format_id": "best_video",
            "type": "video",
            "label": "Best Video Quality",
            "resolution": "auto",
            "ext": "mp4",
            "quality_note": "Direct stream" if (not ffmpeg_available or best_direct_video) else "High Definition",
            "requires_ffmpeg": False if (best_direct_video or not ffmpeg_available) else True,
            "available": True,
            "approx_size_bytes": None,
        })

    # Add audio options
    # MP3 (requires ffmpeg conversion if source is m4a/opus/webm)
    processed_formats.append({
        "format_id": "audio_mp3",
        "type": "audio",
        "label": "MP3 Audio (Highest Quality)",
        "resolution": "audio",
        "ext": "mp3",
        "quality_note": "Stereo 192+ kbps" if ffmpeg_available else "Requires FFmpeg (Native audio will be delivered)",
        "requires_ffmpeg": True,
        "available": True,  # If ffmpeg is absent, backend falls back to native audio stream (m4a/webm) safely
        "approx_size_bytes": None,
    })

    # Add M4A / AAC native audio option (playable on all Apple/Android/Web devices without transcode)
    processed_formats.append({
        "format_id": "audio_m4a",
        "type": "audio",
        "label": "M4A / AAC Audio",
        "resolution": "audio",
        "ext": "m4a",
        "quality_note": "Direct high quality audio",
        "requires_ffmpeg": False,
        "available": True,
        "approx_size_bytes": None,
    })

    duration_sec = info.get("duration")

    return {
        "id": str(info.get("id", "")),
        "title": info.get("title") or "Unknown Title",
        "thumbnail": info.get("thumbnail"),
        "duration": duration_sec,
        "duration_formatted": format_duration(duration_sec),
        "uploader": info.get("uploader") or info.get("channel") or info.get("creator") or "Unknown Creator",
        "view_count": info.get("view_count"),
        "platform": get_platform_name(info.get("extractor"), url),
        "original_url": url,
        "formats": processed_formats,
        "ffmpeg_available": ffmpeg_available,
    }
