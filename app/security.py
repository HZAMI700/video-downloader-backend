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

    # Pinterest (pin.it, pinterest.com, regional domains)
    if "pinterest." in hostname or "pin.it" in hostname:
        return "Pinterest"

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


def is_safe_public_url(url_str: str) -> bool:
    """Helper to check if a target redirect URL is safe and not an SSRF attempt."""
    try:
        parsed = urlparse(url_str)
        if parsed.scheme.lower() not in ("http", "https"):
            return False
        hostname = (parsed.hostname or "").lower()
        if not hostname or hostname in ("localhost", "local", "broadcasthost") or hostname.endswith(".local") or hostname.endswith(".internal"):
            return False
        if is_ip_private_or_restricted(hostname):
            return False
        addr_info = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        for entry in addr_info:
            ip_str = entry[4][0]
            if is_ip_private_or_restricted(ip_str):
                return False
        return True
    except Exception:
        return False


def resolve_shortlink_url(url: str, timeout: int = 5) -> str:
    """
    Resolve known shortlinks or share URLs (pin.it, vm.tiktok.com, fb.watch, facebook.com/share/)
    to their canonical target URL before validation or extraction.
    Ensures safe redirects without allowing SSRF to private/internal addresses.
    """
    if not url or not isinstance(url, str):
        return url

    url_str = url.strip()
    try:
        parsed = urlparse(url_str)
        hostname = (parsed.hostname or "").lower()
    except Exception:
        return url_str

    needs_resolve = (
        "pin.it" in hostname
        or "vm.tiktok.com" in hostname
        or "vt.tiktok.com" in hostname
        or "fb.watch" in hostname
        or ("facebook.com" in hostname and "/share/" in url_str)
        or ("tiktok.com" in hostname and "/t/" in url_str)
        or "youtu.be" in hostname
    )

    if not needs_resolve:
        return url_str

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    current = url_str
    try:
        import requests
        from urllib.parse import urljoin
        for _ in range(5):
            r = requests.get(current, headers=headers, allow_redirects=False, timeout=timeout)
            loc = r.headers.get("Location")
            if not loc:
                break
            if loc.startswith("/"):
                next_url = urljoin(current, loc)
            else:
                next_url = loc
            
            # Security: ensure redirect hop is safe and public
            if not is_safe_public_url(next_url):
                break
            current = next_url

            if "/pin/" in current and "url_shortener" not in current:
                break
            if "facebook.com/reel/" in current or "facebook.com/watch" in current:
                break
            if "tiktok.com/@" in current and "/video/" in current:
                break
    except Exception:
        pass

    return current



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
