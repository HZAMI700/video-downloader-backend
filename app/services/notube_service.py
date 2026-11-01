import logging
import random
import re
import time
import urllib.parse
from typing import Dict, Any, List, Optional
import requests
from bs4 import BeautifulSoup
from app.security import resolve_shortlink_url

logger = logging.getLogger("video_downloader.notube")

# List of noTube conversion backend worker servers
NOTUBE_SERVERS: List[int] = [
    40, 41, 42, 43, 44, 45, 46, 47, 48, 49,
    50, 51, 52, 53, 54, 55, 56, 57, 58, 59,
    60, 61, 62, 63, 64, 65, 66, 67, 68, 69,
    70, 71, 72, 73, 74, 75, 76
]

_LAST_HEALTHY_SERVER: Optional[int] = None
_LAST_HEALTHY_TIME: float = 0.0

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Origin": "https://notube.lol",
    "Referer": "https://notube.lol/fr/youtube-app-449",
    "Accept": "*/*",
}


def find_live_notube_server() -> int:
    """
    Selects a responsive, low-latency noTube worker node.
    Caches the healthy server for 3 minutes to avoid ping overhead.
    """
    global _LAST_HEALTHY_SERVER, _LAST_HEALTHY_TIME

    now = time.time()
    if _LAST_HEALTHY_SERVER and (now - _LAST_HEALTHY_TIME < 180.0):
        return _LAST_HEALTHY_SERVER

    candidates = list(NOTUBE_SERVERS)
    random.shuffle(candidates)

    for server_num in candidates[:8]:
        url = f"https://s{server_num}.notube.lol/weight.php"
        try:
            r = requests.get(url, headers=DEFAULT_HEADERS, timeout=2.0)
            if r.status_code == 200:
                _LAST_HEALTHY_SERVER = server_num
                _LAST_HEALTHY_TIME = now
                logger.info(f"Connected to healthy noTube worker server: s{server_num}")
                return server_num
        except Exception:
            continue

    return _LAST_HEALTHY_SERVER or 42


def map_format_to_notube(format_id: str) -> str:
    """Maps internal format IDs to noTube API format identifiers."""
    fid = format_id.lower().strip()
    if fid in ("audio_mp3", "mp3", "audio", "mp3_320k"):
        return "mp3"
    if fid in ("audio_wav", "wav"):
        return "wav"
    if fid in ("audio_m4a", "m4a"):
        return "m4a"
    if fid in ("video_1080p", "mp4hd", "1080p", "best_video"):
        return "mp4hd"
    if fid in ("video_720p", "720p", "mp4", "video_480p", "video_360p"):
        return "mp4"
    return "mp3" if "audio" in fid else "mp4"


def convert_youtube_via_notube(url: str, format_id: str = "mp3") -> Optional[Dict[str, Any]]:
    """
    Converts YouTube media to MP3 or MP4 using noTube's high-speed distributed engine.
    Executes 4-phase conversion handshake:
    1. recover_weight.php (initializes token & metadata)
    2. recover_file.php (triggers server-side stream conversion)
    3. conversion.php (finalizes packaging)
    4. download page resolution (extracts signed download stream link)
    """
    resolved_url = resolve_shortlink_url(url)
    fmt = map_format_to_notube(format_id)

    # Attempt conversion across up to 3 worker nodes
    tested_servers = set()
    for attempt in range(3):
        server_id = find_live_notube_server()
        while server_id in tested_servers and len(tested_servers) < len(NOTUBE_SERVERS):
            server_id = random.choice(NOTUBE_SERVERS)
        tested_servers.add(server_id)

        base_url = f"https://s{server_id}.notube.lol"
        logger.info(f"[Attempt {attempt + 1}] Invoking noTube server s{server_id} for {fmt}: {resolved_url}")

        try:
            # Step 1: recover_weight.php
            rw_payload = {
                "url": resolved_url,
                "format": fmt,
                "lang": "fr",
                "subscribed": "false",
            }
            rw_res = requests.post(
                f"{base_url}/recover_weight.php",
                data=rw_payload,
                headers=DEFAULT_HEADERS,
                timeout=12,
            )
            if rw_res.status_code != 200 or not rw_res.text.strip():
                logger.warning(f"noTube s{server_id} recover_weight returned status {rw_res.status_code}")
                continue

            rw_data = rw_res.json()
            token = rw_data.get("token")
            name_mp4 = rw_data.get("name_mp4")
            raw_title = rw_data.get("titre_mp4", "")
            title = urllib.parse.unquote_plus(raw_title).strip() if raw_title else "YouTube Video"

            if not token:
                logger.warning(f"noTube s{server_id} returned no token in recover_weight")
                continue

            # Step 2: recover_file.php
            rf_payload = {
                "url": resolved_url,
                "format": fmt,
                "name_mp4": name_mp4,
                "lang": "fr",
                "token": token,
                "subscribed": "false",
                "playlist": "false",
                "adblock": "false",
            }
            rf_res = requests.post(
                f"{base_url}/recover_file.php",
                data=rf_payload,
                headers=DEFAULT_HEADERS,
                timeout=45,
            )
            if rf_res.status_code != 200:
                logger.warning(f"noTube s{server_id} recover_file failed with status {rf_res.status_code}")
                continue

            # Step 3: conversion.php
            try:
                requests.post(
                    f"{base_url}/conversion.php",
                    data={"token": token},
                    headers=DEFAULT_HEADERS,
                    timeout=20,
                )
            except Exception as conv_err:
                logger.debug(f"noTube conversion step non-critical notice: {conv_err}")

            # Step 4: Extract downloadButton URL from download result page
            dl_page = requests.get(
                f"https://notube.lol/fr/download?token={token}",
                headers=DEFAULT_HEADERS,
                timeout=15,
            )
            if dl_page.status_code != 200:
                logger.warning(f"noTube download page returned status {dl_page.status_code}")
                continue

            soup = BeautifulSoup(dl_page.text, "html.parser")
            btn = soup.find("a", id="downloadButton")
            if btn and btn.get("href"):
                dl_url = btn.get("href")
                logger.info(f"noTube conversion successful on s{server_id}! Direct stream URL: {dl_url}")
                return {
                    "title": title,
                    "direct_url": dl_url,
                    "token": token,
                    "format": fmt,
                    "ext": "mp3" if fmt in ("mp3", "wav", "m4a") else "mp4",
                    "server_id": server_id,
                }
            else:
                logger.warning(f"Download button missing on download page for token {token}")

        except Exception as exc:
            logger.warning(f"noTube worker s{server_id} failed: {exc}")
            continue

    return None


def extract_youtube_metadata_via_oembed(url: str) -> Optional[Dict[str, Any]]:
    """
    Extracts core YouTube video details via YouTube's official oEmbed service.
    Fast, reliable, zero bot detection blocks or login challenges.
    """
    resolved = resolve_shortlink_url(url)
    m = re.search(r"(?:v=|\/|be\/)([0-9A-Za-z_-]{11})", resolved)
    if not m:
        return None

    video_id = m.group(1)
    canonical_url = f"https://www.youtube.com/watch?v={video_id}"

    title = "YouTube Video"
    uploader = "YouTube Creator"
    thumb = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

    try:
        r = requests.get(
            f"https://www.youtube.com/oembed?url={urllib.parse.quote(canonical_url)}&format=json",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=5,
        )
        if r.status_code == 200:
            data = r.json()
            title = data.get("title") or title
            uploader = data.get("author_name") or uploader
            if data.get("thumbnail_url"):
                thumb = data.get("thumbnail_url")
    except Exception as e:
        logger.debug(f"oEmbed request failed: {e}")

    # Standard YouTube format offerings
    formats = [
        {
            "format_id": "video_1080p",
            "type": "video",
            "label": "MP4 1080p Full HD",
            "resolution": "1080p",
            "ext": "mp4",
            "quality_note": "High Bitrate 1080p Video",
            "requires_ffmpeg": False,
            "available": True,
            "approx_size_bytes": 28 * 1024 * 1024,
            "direct_url": None,
        },
        {
            "format_id": "video_720p",
            "type": "video",
            "label": "MP4 720p HD",
            "resolution": "720p",
            "ext": "mp4",
            "quality_note": "Standard High Definition",
            "requires_ffmpeg": False,
            "available": True,
            "approx_size_bytes": 14 * 1024 * 1024,
            "direct_url": None,
        },
        {
            "format_id": "video_480p",
            "type": "video",
            "label": "MP4 480p SD",
            "resolution": "480p",
            "ext": "mp4",
            "quality_note": "Standard Definition",
            "requires_ffmpeg": False,
            "available": True,
            "approx_size_bytes": 8 * 1024 * 1024,
            "direct_url": None,
        },
        {
            "format_id": "audio_mp3",
            "type": "audio",
            "label": "MP3 (320kbps Audio)",
            "resolution": "320kbps",
            "ext": "mp3",
            "quality_note": "Crystal Pure MP3 Stream",
            "requires_ffmpeg": True,
            "available": True,
            "approx_size_bytes": 5 * 1024 * 1024,
            "direct_url": None,
        },
        {
            "format_id": "audio_wav",
            "type": "audio",
            "label": "WAV (Lossless Audio)",
            "resolution": "Lossless",
            "ext": "wav",
            "quality_note": "Bit-Perfect Studio Audio",
            "requires_ffmpeg": True,
            "available": True,
            "approx_size_bytes": 35 * 1024 * 1024,
            "direct_url": None,
        },
    ]

    return {
        "id": f"yt_{video_id}",
        "title": title,
        "thumbnail": thumb,
        "duration": None,
        "duration_formatted": None,
        "uploader": uploader,
        "view_count": None,
        "platform": "YouTube",
        "original_url": canonical_url,
        "formats": formats,
        "ffmpeg_available": True,
        "direct_media_url": None,
    }
