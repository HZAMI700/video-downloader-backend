import ipaddress
import re
import socket
from urllib.parse import urlparse
from fastapi import HTTPException


def is_ip_private_or_restricted(ip_str: str) -> bool:
    """Check if an IP address is private, loopback, link-local, multicast, or reserved."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )
    except ValueError:
        return False


def detect_media_platform(url: str) -> str:
    """
    Detect media platform from URL (YouTube, TikTok, Instagram, Facebook, etc.).
    Supports standard URLs, short links (youtu.be, vm.tiktok.com, fb.watch), reels, and mobile links.
    """
    if not url or not isinstance(url, str):
        return "Unknown"

    try:
        parsed = urlparse(url.strip())
        hostname = (parsed.hostname or "").lower()
    except Exception:
        return "Unknown"

    # YouTube (watch, shorts, youtu.be, m.youtube.com)
    if "youtube.com" in hostname or "youtu.be" in hostname:
        return "YouTube"

    # TikTok (standard, vm.tiktok.com, vt.tiktok.com, m.tiktok.com)
    if "tiktok.com" in hostname:
        return "TikTok"

    # Instagram (posts, reels, stories, tv, instagr.am)
    if "instagram.com" in hostname or "instagr.am" in hostname:
        return "Instagram"

    # Facebook (watch, reels, fb.watch, fb.com, m.facebook.com)
    if "facebook.com" in hostname or "fb.watch" in hostname or "fb.com" in hostname:
        return "Facebook"

    # Twitter / X
    if "twitter.com" in hostname or "x.com" in hostname:
        return "X (Twitter)"

    # Reddit
    if "reddit.com" in hostname or "redd.it" in hostname:
        return "Reddit"

    # Vimeo
    if "vimeo.com" in hostname:
        return "Vimeo"

    # SoundCloud
    if "soundcloud.com" in hostname:
        return "SoundCloud"

    # Twitch
    if "twitch.tv" in hostname:
        return "Twitch"

    return "Web Media"


def validate_media_url(url: str) -> str:
    """
    Validate that a given URL is a legitimate public web URL.
    Protects against SSRF, internal network scanning, and invalid schemes.
    """
    if not url or not isinstance(url, str):
        raise HTTPException(status_code=400, detail="A valid media URL must be provided.")

    url = url.strip()
    if len(url) > 2048:
        raise HTTPException(status_code=400, detail="URL exceeds maximum allowed length of 2048 characters.")

    try:
        parsed = urlparse(url)
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed URL structure.")

    if parsed.scheme.lower() not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Only HTTP and HTTPS URLs are supported.")

    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(status_code=400, detail="URL must contain a valid domain name or host.")

    hostname_lower = hostname.lower()

    # Block well-known local/internal hostnames
    if (
        hostname_lower in ("localhost", "local", "broadcasthost")
        or hostname_lower.endswith(".local")
        or hostname_lower.endswith(".internal")
    ):
        raise HTTPException(status_code=400, detail="Requests to local or internal network targets are prohibited.")

    # Check if host is direct IP
    if is_ip_private_or_restricted(hostname_lower):
        raise HTTPException(status_code=400, detail="Target host resolves to a restricted or private network address.")

    # Resolve hostname via DNS and check resolved IP
    try:
        addr_info = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        for entry in addr_info:
            ip_str = entry[4][0]
            if is_ip_private_or_restricted(ip_str):
                raise HTTPException(status_code=400, detail="Target host resolves to a restricted or private network address.")
    except socket.gaierror:
        # If DNS fails, fail fast with a friendly message
        raise HTTPException(status_code=400, detail=f"Unable to resolve domain: '{hostname}'. Please check the URL.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Validation failed for host '{hostname}': {str(e)}")

    return url


def sanitize_filename(filename: str, default: str = "media_download") -> str:
    """
    Sanitize a filename to avoid path traversal, reserved characters, and control characters.
    """
    if not filename:
        return default

    # Remove path separators
    filename = filename.replace("\\", "_").replace("/", "_")
    # Replace non-printable and dangerous chars
    filename = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]', '_', filename)
    # Collapse multiple spaces or underscores
    filename = re.sub(r'[\s_]+', '_', filename).strip("._ ")
    # Limit length
    if len(filename) > 120:
        filename = filename[:120].rstrip("._ ")

    return filename or default
