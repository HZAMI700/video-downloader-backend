import logging
import re
from typing import Dict, Any, Optional
import requests
from app.security import detect_media_platform, resolve_shortlink_url

logger = logging.getLogger("video_downloader.cobalt")

COBALT_ENDPOINT = "https://dwnld.nichind.dev/"
COBALT_API_KEY = "b05007aa-bb63-4267-a66e-78f8e10bf9bf"


def extract_cobalt_media(url: str, is_audio_only: bool = False) -> Optional[Dict[str, Any]]:
    """
    Extract media download streams using the Cobalt processing engine.
    Supports TikTok (no watermark), Facebook (reels/watch), Instagram, and YouTube.
    Bypasses datacenter IP blocks, Cloudflare Turnstile, and platform WAF challenges.
    """
    resolved_url = resolve_shortlink_url(url)
    platform = detect_media_platform(resolved_url)

    target_url = resolved_url
    if platform == "Facebook":
        # Cobalt Facebook parser requires canonical watch parameters
        reel_m = re.search(r'facebook\.com/reel/(\d+)', resolved_url)
        if reel_m:
            target_url = f"https://www.facebook.com/watch/?v={reel_m.group(1)}"

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Api-Key {COBALT_API_KEY}",
        "User-Agent": "PlayzyDownloader/1.0",
    }

    payload = {
        "url": target_url,
        "isAudioOnly": is_audio_only,
        "filenameStyle": "pretty",
    }

    try:
        r = requests.post(COBALT_ENDPOINT, json=payload, headers=headers, timeout=12)
        if r.status_code != 200:
            logger.warning(f"Cobalt returned HTTP {r.status_code} for {target_url}")
            return None

        res = r.json()
        status = res.get("status")

        if status in ("redirect", "tunnel") and res.get("url"):
            direct_url = res["url"]
            filename = res.get("filename") or f"{platform.lower()}_download.mp4"
            thumb = res.get("thumb") or res.get("thumbnail")
            
            # Clean title from filename
            clean_title = re.sub(r'^[a-zA-Z0-9]+_', '', filename)
            clean_title = re.sub(r'\.[a-zA-Z0-9]+$', '', clean_title).replace('_', ' ').strip()
            if not clean_title or len(clean_title) < 3:
                clean_title = f"{platform} Video"

            formats = [
                {
                    "format_id": "video_1080p",
                    "type": "video",
                    "label": f"MP4 1080p Full HD{' (No Watermark)' if platform == 'TikTok' else ''}",
                    "resolution": "1080p",
                    "ext": "mp4",
                    "quality_note": "High Bitrate Direct Stream",
                    "requires_ffmpeg": False,
                    "available": True,
                    "approx_size_bytes": 15 * 1024 * 1024,
                    "direct_url": direct_url,
                },
                {
                    "format_id": "video_720p",
                    "type": "video",
                    "label": f"MP4 720p HD{' (No Watermark)' if platform == 'TikTok' else ''}",
                    "resolution": "720p",
                    "ext": "mp4",
                    "quality_note": "Standard High Definition",
                    "requires_ffmpeg": False,
                    "available": True,
                    "approx_size_bytes": 8 * 1024 * 1024,
                    "direct_url": direct_url,
                },
                {
                    "format_id": "audio_mp3",
                    "type": "audio",
                    "label": "MP3 (320kbps Audio)",
                    "resolution": "320kbps",
                    "ext": "mp3",
                    "quality_note": "High Bitrate Crystal Audio",
                    "requires_ffmpeg": True,
                    "available": True,
                    "approx_size_bytes": 4 * 1024 * 1024,
                    "direct_url": direct_url,
                }
            ]

            return {
                "id": f"cobalt_{platform.lower()}",
                "title": clean_title,
                "thumbnail": thumb,
                "duration": None,
                "duration_formatted": None,
                "uploader": f"{platform} Creator",
                "view_count": None,
                "platform": platform,
                "original_url": resolved_url,
                "formats": formats,
                "ffmpeg_available": True,
                "direct_media_url": direct_url,
            }

        elif status == "picker" and res.get("picker"):
            # Multi-item post (e.g. photos/videos)
            items = res["picker"]
            if items and items[0].get("url"):
                first_url = items[0]["url"]
                return {
                    "id": f"cobalt_{platform.lower()}",
                    "title": f"{platform} Media",
                    "thumbnail": items[0].get("thumb"),
                    "duration": None,
                    "duration_formatted": None,
                    "uploader": f"{platform} Creator",
                    "view_count": None,
                    "platform": platform,
                    "original_url": resolved_url,
                    "formats": [
                        {
                            "format_id": "best_video",
                            "type": "video",
                            "label": "MP4 (Best Quality)",
                            "resolution": "HD",
                            "ext": "mp4",
                            "quality_note": "Direct Media Stream",
                            "requires_ffmpeg": False,
                            "available": True,
                            "approx_size_bytes": 10 * 1024 * 1024,
                            "direct_url": first_url,
                        }
                    ],
                    "ffmpeg_available": True,
                    "direct_media_url": first_url,
                }

    except Exception as e:
        logger.warning(f"Cobalt request failed for {resolved_url}: {e}")

    return None
