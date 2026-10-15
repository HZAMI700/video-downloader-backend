import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
import requests
import yt_dlp
from fastapi import HTTPException
from app.config import settings
from app.security import detect_media_platform, resolve_shortlink_url
from app.services.cobalt_service import extract_cobalt_media

logger = logging.getLogger("video_downloader.ytdlp")

_FFMPEG_AVAILABLE: Optional[bool] = None


def find_cookies_file() -> Optional[str]:
    """
    Search for a valid cookies file or write COOKIES_TEXT if provided via environment variable.
    Supports Netscape cookies format exported from browsers for Instagram, YouTube, etc.
    """
    # 1. Check if COOKIES_TEXT environment variable is set
    cookies_text = os.getenv("COOKIES_TEXT")
    if cookies_text and cookies_text.strip():
        try:
            target_path = settings.temp_dir_path / "imported_cookies.txt"
            target_path.write_text(cookies_text.strip(), encoding="utf-8")
            return str(target_path)
        except Exception as e:
            logger.warning(f"Failed to write COOKIES_TEXT to file: {e}")

    # 2. Check explicit COOKIES_FILE env var
    env_file = os.getenv("COOKIES_FILE")
    if env_file and os.path.isfile(env_file):
        return env_file

    # 3. Check well-known local paths
    candidates = [
        Path("cookies.txt"),
        Path(__file__).resolve().parent.parent.parent / "cookies.txt",
        settings.temp_dir_path.parent / "cookies.txt",
        settings.temp_dir_path / "cookies.txt",
        Path("instagram_cookies.txt"),
        Path(__file__).resolve().parent.parent.parent / "instagram_cookies.txt",
    ]
    for c in candidates:
        if c.is_file() and c.stat().st_size > 0:
            return str(c.resolve())

    return None


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


def classify_extraction_error(e: Exception, platform: str, url: str) -> HTTPException:
    """
    Classify yt-dlp or platform extraction exceptions into structured HTTP exceptions.
    Never exposes internal tokens, cookies, or raw tracebacks to the client.
    """
    msg = str(e)
    clean_msg = re.sub(r'https?://[^\s<>"]+', '', msg).strip()

    if any(term in msg.lower() for term in ["private video", "is private", "this content isn't available right now", "requires approval"]):
        return HTTPException(status_code=403, detail=f"This {platform} video is private or restricted to authorized viewers.")

    if any(term in msg.lower() for term in ["sign in", "login", "requires authentication", "log in"]):
        return HTTPException(status_code=403, detail=f"This {platform} video requires an account login to view.")

    if any(term in msg.lower() for term in ["video unavailable", "does not exist", "removed", "deleted", "not found", "status code 404"]):
        return HTTPException(status_code=404, detail=f"This {platform} video was removed, deleted, or does not exist.")

    if any(term in msg.lower() for term in ["blocked from accessing", "challenge", "captcha", "anti-bot", "bot detection"]):
        return HTTPException(status_code=503, detail=f"Temporary {platform} security challenge encountered. Please retry shortly.")

    if any(term in msg.lower() for term in ["rate limit", "too many requests", "429"]):
        return HTTPException(status_code=429, detail=f"{platform} request limit reached. Please wait a moment before trying again.")

    if any(term in msg.lower() for term in ["timed out", "timeout", "connection refused"]):
        return HTTPException(status_code=504, detail=f"Connection to {platform} timed out. Please check the link and try again.")

    if "unsupported url" in msg.lower():
        return HTTPException(status_code=400, detail=f"The URL provided is not a supported {platform} media link.")

    if "instagram sent an empty media response" in msg.lower() or "login_required" in msg.lower():
        return HTTPException(status_code=403, detail="Instagram requires account authentication to view this post. Please ensure the post is public.")

    return HTTPException(status_code=400, detail=f"Unable to extract video details from {platform}. The media may be restricted or in an unsupported format.")


def extract_tiktok_metadata(url: str) -> Optional[Dict[str, Any]]:
    """
    Extract TikTok video metadata and direct MP4 streams without watermarks.
    Uses TikWM API first (bypassing cloud/datacenter IP challenge locks), with fallback to yt-dlp.
    """
    resolved_url = resolve_shortlink_url(url)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
    }

    try:
        r = requests.post("https://www.tikwm.com/api/", data={"url": resolved_url}, headers=headers, timeout=12)
        if r.status_code == 200:
            res = r.json()
            if res.get("code") == 0 and res.get("data"):
                d = res["data"]
                title = d.get("title") or "TikTok Video"
                dur = int(round(d.get("duration") or 0))
                uploader = (d.get("author") or {}).get("nickname") or (d.get("author") or {}).get("unique_id") or "TikTok Creator"
                play_url = d.get("play")
                hd_play_url = d.get("hdplay")
                music_url = d.get("music")
                cover = d.get("cover") or d.get("origin_cover")
                video_id = str(d.get("id") or "tiktok_video")

                formats = []
                if hd_play_url:
                    formats.append({
                        "format_id": "video_1080p",
                        "type": "video",
                        "label": "MP4 1080p Full HD (No Watermark)",
                        "resolution": "1080p",
                        "ext": "mp4",
                        "quality_note": "Crystal High Bitrate Without Watermark",
                        "requires_ffmpeg": False,
                        "available": True,
                        "approx_size_bytes": d.get("size") or (15 * 1024 * 1024),
                        "direct_url": hd_play_url,
                    })
                if play_url:
                    formats.append({
                        "format_id": "video_720p",
                        "type": "video",
                        "label": "MP4 (No Watermark)",
                        "resolution": "720p",
                        "ext": "mp4",
                        "quality_note": "Original Video Without Watermark",
                        "requires_ffmpeg": False,
                        "available": True,
                        "approx_size_bytes": d.get("size") or (8 * 1024 * 1024),
                        "direct_url": play_url,
                    })
                if music_url:
                    formats.append({
                        "format_id": "audio_mp3",
                        "type": "audio",
                        "label": "MP3 (320kbps Audio)",
                        "resolution": "320kbps",
                        "ext": "mp3",
                        "quality_note": "Original Soundtrack",
                        "requires_ffmpeg": False,
                        "available": True,
                        "approx_size_bytes": 3 * 1024 * 1024,
                        "direct_url": music_url,
                    })

                ffmpeg_available = check_ffmpeg_installed()
                return {
                    "id": video_id,
                    "title": title,
                    "thumbnail": cover,
                    "duration": dur,
                    "duration_formatted": format_duration(dur),
                    "uploader": uploader,
                    "view_count": d.get("play_count"),
                    "platform": "TikTok",
                    "original_url": resolved_url,
                    "formats": formats,
                    "ffmpeg_available": ffmpeg_available,
                    "direct_media_url": hd_play_url or play_url,
                }
    except Exception as e:
        logger.warning(f"TikWM extraction failed for {resolved_url}: {e}")

    return None


def extract_facebook_direct_metadata(url: str) -> Optional[Dict[str, Any]]:
    """
    Extract Facebook video metadata directly by scraping public OpenGraph and embedded JSON streams.
    Serves as a reliable fallback when yt-dlp's mobile parser encounters schema changes.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        r = requests.get(url, headers=headers, timeout=12)
        html = r.text

        hd_match = re.search(r'"browser_native_hd_url"\s*:\s*"([^"]+)"', html)
        sd_match = re.search(r'"browser_native_sd_url"\s*:\s*"([^"]+)"', html)
        playable_match = re.search(r'"playable_url"\s*:\s*"([^"]+)"', html)
        og_match = re.search(r'<meta[^>]*property="og:video"[^>]*content="([^"]+)"', html)

        hd_url = hd_match.group(1).replace(r'\/', '/').encode().decode('unicode-escape', errors='ignore') if hd_match else None
        sd_url = sd_match.group(1).replace(r'\/', '/').encode().decode('unicode-escape', errors='ignore') if sd_match else None
        play_url = playable_match.group(1).replace(r'\/', '/').encode().decode('unicode-escape', errors='ignore') if playable_match else (og_match.group(1) if og_match else None)

        best_stream = hd_url or sd_url or play_url
        if not best_stream:
            return None

        title_m = re.search(r'<title>(.*?)</title>', html)
        title = title_m.group(1).replace(' | Facebook', '').replace('Facebook', '').strip() if title_m else "Facebook Video"
        if not title:
            title = "Facebook Video"

        og_thumb = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]+)"', html)
        thumb = og_thumb.group(1) if og_thumb else None

        formats = []
        if hd_url:
            formats.append({
                "format_id": "video_1080p",
                "type": "video",
                "label": "MP4 1080p Full HD",
                "resolution": "1080p",
                "ext": "mp4",
                "quality_note": "High Definition Progressive MP4",
                "requires_ffmpeg": False,
                "available": True,
                "approx_size_bytes": 25 * 1024 * 1024,
                "direct_url": hd_url,
            })
        if sd_url or play_url:
            formats.append({
                "format_id": "video_720p",
                "type": "video",
                "label": "MP4 720p HD",
                "resolution": "720p",
                "ext": "mp4",
                "quality_note": "Standard High Definition",
                "requires_ffmpeg": False,
                "available": True,
                "approx_size_bytes": 15 * 1024 * 1024,
                "direct_url": sd_url or play_url,
            })

        ffmpeg_available = check_ffmpeg_installed()
        formats.append({
            "format_id": "audio_mp3",
            "type": "audio",
            "label": "MP3 (320kbps Audio)",
            "resolution": "320kbps",
            "ext": "mp3",
            "quality_note": "High Bitrate Crystal Audio",
            "requires_ffmpeg": True,
            "available": True,
            "approx_size_bytes": 4 * 1024 * 1024,
            "direct_url": best_stream,
        })

        return {
            "id": "fb_video",
            "title": title,
            "thumbnail": thumb,
            "duration": None,
            "duration_formatted": None,
            "uploader": "Facebook Creator",
            "view_count": None,
            "platform": "Facebook",
            "original_url": url,
            "formats": formats,
            "ffmpeg_available": ffmpeg_available,
            "direct_media_url": best_stream,
        }
    except Exception as e:
        logger.warning(f"Facebook direct extraction failed: {e}")
        return None


def extract_media_metadata(url: str, request_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Extract video metadata using multi-tier extractors (TikTok/Pinterest direct engines + yt-dlp).
    Returns structured details, accurate durations, and supported formats.
    """
    ffmpeg_available = check_ffmpeg_installed()
    resolved_url = resolve_shortlink_url(url)
    detected_platform = detect_media_platform(resolved_url)

    # 1. Route Pinterest directly to our reliable scraper
    if detected_platform == "Pinterest":
        return extract_pinterest_metadata(resolved_url)

    # 2. Route TikTok to TikWM direct engine first to prevent datacenter IP blocks, with Cobalt fallback
    if detected_platform == "TikTok":
        tt_data = extract_tiktok_metadata(resolved_url)
        if tt_data:
            logger.info(f"[{request_id or 'anon'}] TikTok extracted successfully via TikWM pipeline")
            return tt_data
        cobalt_data = extract_cobalt_media(resolved_url)
        if cobalt_data:
            logger.info(f"[{request_id or 'anon'}] TikTok extracted successfully via Cobalt pipeline")
            return cobalt_data

    # 3. For Facebook, normalize reel links and try high-speed Cobalt engine first
    target_extract_url = resolved_url
    if detected_platform == "Facebook":
        reel_m = re.search(r'facebook\.com/reel/(\d+)', resolved_url)
        if reel_m:
            target_extract_url = f"https://www.facebook.com/watch/?v={reel_m.group(1)}"
        cobalt_data = extract_cobalt_media(target_extract_url) or extract_cobalt_media(resolved_url)
        if cobalt_data:
            logger.info(f"[{request_id or 'anon'}] Facebook extracted successfully via Cobalt pipeline")
            return cobalt_data

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

    cookie_file = find_cookies_file()
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file
        logger.info(f"[{request_id or 'anon'}] Using cookies file: {cookie_file}")

    # 4. For Instagram, attempt high-speed Cobalt engine first if no cookies file is configured
    if detected_platform == "Instagram" and not cookie_file:
        cobalt_data = extract_cobalt_media(resolved_url)
        if cobalt_data:
            logger.info(f"[{request_id or 'anon'}] Instagram extracted successfully via Cobalt pipeline")
            return cobalt_data

    info = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target_extract_url, download=False)
    except Exception as e:
        logger.warning(f"[{request_id or 'anon'}] yt-dlp extraction failed for {detected_platform} ({target_extract_url}): {e}")

        # Universal fallback to Cobalt engine if yt-dlp failed
        cobalt_data = extract_cobalt_media(target_extract_url) or extract_cobalt_media(resolved_url)
        if cobalt_data:
            logger.info(f"[{request_id or 'anon'}] {detected_platform} Cobalt fallback succeeded")
            return cobalt_data

        # If Facebook fails in yt-dlp and Cobalt, trigger direct HTML scraper fallback
        if detected_platform == "Facebook":
            fb_data = extract_facebook_direct_metadata(resolved_url)
            if fb_data:
                logger.info(f"[{request_id or 'anon'}] Facebook direct HTML fallback succeeded")
                return fb_data

        # If TikTok was bypassed or failed earlier, recheck TikWM
        if detected_platform == "TikTok":
            tt_data = extract_tiktok_metadata(resolved_url)
            if tt_data:
                return tt_data

        raise classify_extraction_error(e, detected_platform, resolved_url)

    if not info:
        raise HTTPException(status_code=404, detail=f"No media information found for the requested {detected_platform} URL.")

    # Handle playlist/carousel posts (common for Instagram /p/ posts with multiple photos or videos)
    if "entries" in info and info["entries"]:
        entries = [e for e in info["entries"] if e]
        if entries:
            first_entry = entries[0]
            for k in ("title", "id", "thumbnail", "uploader", "view_count", "duration"):
                if not first_entry.get(k) and info.get(k):
                    first_entry[k] = info[k]
            info = first_entry

    # Process formats
    raw_formats = info.get("formats", [])
    duration_sec = info.get("duration")

    processed_formats: List[Dict[str, Any]] = []
    seen_resolutions = set()

    # Collect available video resolutions
    video_map: Dict[str, Dict[str, Any]] = {}
    best_single_format: Optional[Dict[str, Any]] = None
    has_video_track = False

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

        if is_video:
            has_video_track = True

        # For vertical videos (Reels, TikTok, Shorts), the shorter dimension indicates the standard quality scale (e.g. 1080x1920 is 1080p)
        effective_height = min(width, height) if (width and height) else height

        # Special handling for Facebook 'hd' and 'sd'
        if "hd" in f_id.lower() and not effective_height:
            effective_height = 1080
            is_video = True
            has_video_track = True
        elif "sd" in f_id.lower() and not effective_height:
            effective_height = 480
            is_video = True
            has_video_track = True

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

    # Check if this is an image / photo post (common for Instagram /p/ photo posts)
    is_image_post = (
        not has_video_track
        or info.get("is_video") is False
        or info.get("ext") in ("jpg", "jpeg", "png", "webp")
    )
    direct_image_url = info.get("url") or info.get("thumbnail")
    if not direct_image_url and info.get("thumbnails"):
        thumbs = sorted(info["thumbnails"], key=lambda t: (t.get("width") or 0) * (t.get("height") or 0), reverse=True)
        if thumbs:
            direct_image_url = thumbs[0].get("url")

    if is_image_post and direct_image_url:
        w = info.get("width")
        h = info.get("height")
        res_label = f"{w}x{h}" if (w and h) else "HD"
        processed_formats.append({
            "format_id": "image_orig",
            "type": "image",
            "label": "Original Photo (High Definition)",
            "resolution": res_label,
            "ext": "jpg",
            "quality_note": "Original Image File",
            "requires_ffmpeg": False,
            "available": True,
            "approx_size_bytes": 2 * 1024 * 1024,
            "direct_url": direct_image_url,
        })
        processed_formats.append({
            "format_id": "image_hd",
            "type": "image",
            "label": "JPEG Image (Standard Quality)",
            "resolution": "Standard",
            "ext": "jpg",
            "quality_note": "Compressed Web JPEG",
            "requires_ffmpeg": False,
            "available": True,
            "approx_size_bytes": 1024 * 1024,
            "direct_url": direct_image_url,
        })
    else:
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
    dur_int = int(round(duration_sec)) if duration_sec is not None else None

    return {
        "id": str(info.get("id", "")),
        "title": info.get("title") or "Untitled Media",
        "thumbnail": info.get("thumbnail") or direct_image_url,
        "duration": dur_int,
        "duration_formatted": format_duration(dur_int),
        "uploader": info.get("uploader") or info.get("channel") or info.get("creator") or "Unknown Creator",
        "view_count": info.get("view_count"),
        "platform": platform_title,
        "original_url": url,
        "formats": processed_formats,
        "ffmpeg_available": ffmpeg_available,
        "direct_media_url": direct_image_url if is_image_post else (best_single_format.get("url") if best_single_format else None),
    }
