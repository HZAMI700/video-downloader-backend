import logging
import re
import shutil
import subprocess
from typing import Dict, Any, List, Optional
import requests
import yt_dlp
from fastapi import HTTPException
from app.config import settings
from app.security import detect_media_platform, resolve_shortlink_url

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


def extract_pinterest_metadata(url: str) -> Dict[str, Any]:
    """
    Extract Pinterest video metadata and direct MP4 streams.
    Bypasses yt-dlp's broken Pinterest JSON API by scraping the public HTML page directly.
    """
    resolved_url = resolve_shortlink_url(url)
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    }
    
    try:
        r = requests.get(resolved_url, headers=headers, timeout=15)
        html = r.text
    except Exception as e:
        logger.error(f"Failed to fetch Pinterest page for {resolved_url}: {e}")
        raise HTTPException(status_code=400, detail="Failed to connect to Pinterest. Please check the URL.")

    # Match Pin ID
    pin_id_m = re.search(r'/pin/(\d+)', resolved_url)
    pin_id = pin_id_m.group(1) if pin_id_m else "pinterest_video"

    # Extract title
    title_m = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
    raw_title = title_m.group(1) if title_m else f"Pinterest Video {pin_id}"
    title = re.sub(r'\s*\|\s*Pinterest\s*$', '', raw_title).strip()
    if not title:
        title = f"Pinterest Video {pin_id}"

    # Extract thumbnail
    og_img = re.search(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']', html)
    thumbnail = og_img.group(1) if og_img else None
    if not thumbnail:
        imgs = re.findall(r'https://i\.pinimg\.com/[^\s"\'<>]+\.jpg', html)
        if imgs:
            originals = [i for i in imgs if '/736x/' in i or '/originals/' in i or '/564x/' in i]
            thumbnail = originals[0] if originals else imgs[0]

    # Find MP4 video URLs
    mp4_urls = set(re.findall(r'https?://[^\s"\'<>]+\.mp4', html))
    if not mp4_urls:
        raise HTTPException(status_code=400, detail="No video stream found for this Pinterest pin. Please ensure the pin contains a video.")

    mc_urls = [u for u in mp4_urls if '/videos/mc/' in u]
    other_urls = [u for u in mp4_urls if '/videos/mc/' not in u]

    def get_resolution_score(u: str) -> int:
        if '1080' in u:
            return 1080
        if '720' in u:
            return 720
        if '540' in u:
            return 540
        if '480' in u:
            return 480
        if '360' in u:
            return 360
        return 240

    if mc_urls:
        mc_urls.sort(key=get_resolution_score, reverse=True)
        best_video_url = mc_urls[0]
    else:
        sorted_others = sorted(other_urls, key=get_resolution_score, reverse=True)
        best_video_url = sorted_others[0]

    best_res = get_resolution_score(best_video_url)
    res_label = f"{best_res}p" if best_res >= 360 else "HD"

    ffmpeg_available = check_ffmpeg_installed()
    formats = [
        {
            "format_id": f"video_{best_res}p" if best_res >= 360 else "best_video",
            "type": "video",
            "label": f"MP4 {res_label}",
            "resolution": res_label,
            "ext": "mp4",
            "quality_note": "Original Pinterest Video Quality",
            "requires_ffmpeg": False,
            "available": True,
            "approx_size_bytes": 15 * 1024 * 1024,
        },
        {
            "format_id": "best_video",
            "type": "video",
            "label": "MP4 (Best Quality)",
            "resolution": "HD",
            "ext": "mp4",
            "quality_note": "Direct High Speed Stream",
            "requires_ffmpeg": False,
            "available": True,
            "approx_size_bytes": 15 * 1024 * 1024,
        },
        {
            "format_id": "audio_mp3",
            "type": "audio",
            "label": "MP3 (320kbps)",
            "resolution": "320kbps",
            "ext": "mp3",
            "quality_note": "Extracted Audio Track",
            "requires_ffmpeg": True,
            "available": True,
            "approx_size_bytes": 4 * 1024 * 1024,
        }
    ]

    return {
        "id": pin_id,
        "title": title,
        "thumbnail": thumbnail,
        "duration": None,
        "duration_formatted": None,
        "uploader": "Pinterest Creator",
        "view_count": None,
        "platform": "Pinterest",
        "original_url": resolved_url,
        "formats": formats,
        "ffmpeg_available": ffmpeg_available,
        "direct_media_url": best_video_url,
    }


def extract_media_metadata(url: str) -> Dict[str, Any]:
    """
    Extract video metadata using yt-dlp supporting YouTube, TikTok, Facebook, Instagram, Pinterest, etc.
    Returns structured details and supported formats.
    """
    ffmpeg_available = check_ffmpeg_installed()
    resolved_url = resolve_shortlink_url(url)
    detected_platform = detect_media_platform(resolved_url)

    # Route Pinterest directly to our reliable scraper
    if detected_platform == "Pinterest":
        return extract_pinterest_metadata(resolved_url)

    ydl_opts: Dict[str, Any] = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "socket_timeout": 30,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(resolved_url, download=False)
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
        logger.error(f"Unexpected error extracting metadata for {resolved_url}: {e}")
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
        width = f.get("width")
        f_id = str(f.get("format_id", ""))
        filesize = f.get("filesize") or f.get("filesize_approx")

        is_video = vcodec != "none"
        is_audio = acodec != "none"
        is_direct = is_video and is_audio

        # For vertical videos (Reels, TikTok, Shorts), the shorter dimension indicates the standard quality scale (e.g. 1080x1920 is 1080p)
        effective_height = min(width, height) if (width and height) else height

        # Special handling for Facebook 'hd' and 'sd'
        if "hd" in f_id.lower() and not effective_height:
            effective_height = 1080
            is_video = True
        elif "sd" in f_id.lower() and not effective_height:
            effective_height = 480
            is_video = True

        if is_video and effective_height:
            res_key = f"{effective_height}p"
            if res_key not in video_map or (is_direct and not video_map[res_key].get("is_direct")):
                video_map[res_key] = {
                    "height": effective_height,
                    "is_direct": is_direct,
                    "filesize": filesize or estimate_size_bytes(duration_sec, effective_height),
                }

        if is_direct:
            if not best_single_format or (effective_height and effective_height > (best_single_format.get("height") or 0)):
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
