import pytest
from app.security import detect_media_platform


def test_youtube_url_detection():
    assert detect_media_platform("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "YouTube"
    assert detect_media_platform("https://youtu.be/dQw4w9WgXcQ") == "YouTube"
    assert detect_media_platform("https://www.youtube.com/shorts/abc123XYZ") == "YouTube"
    assert detect_media_platform("https://m.youtube.com/watch?v=dQw4w9WgXcQ") == "YouTube"


def test_tiktok_url_detection():
    assert detect_media_platform("https://www.tiktok.com/@user/video/1234567890123456789") == "TikTok"
    assert detect_media_platform("https://vm.tiktok.com/ZM8abcde/") == "TikTok"
    assert detect_media_platform("https://vt.tiktok.com/ZS8abcde/") == "TikTok"
    assert detect_media_platform("https://m.tiktok.com/v/1234567890123456789.html") == "TikTok"


def test_facebook_url_detection():
    assert detect_media_platform("https://www.facebook.com/watch/?v=123456789") == "Facebook"
    assert detect_media_platform("https://fb.watch/abcdef123/") == "Facebook"
    assert detect_media_platform("https://www.facebook.com/reel/123456789") == "Facebook"
    assert detect_media_platform("https://m.facebook.com/story.php?story_fbid=123&id=456") == "Facebook"


def test_instagram_url_detection():
    assert detect_media_platform("https://www.instagram.com/p/C4abcde123/") == "Instagram"
    assert detect_media_platform("https://www.instagram.com/reel/C4abcde123/") == "Instagram"
    assert detect_media_platform("https://instagr.am/p/C4abcde123/") == "Instagram"


def test_pinterest_url_detection():
    assert detect_media_platform("https://www.pinterest.com/pin/123456789012345678/") == "Pinterest"
    assert detect_media_platform("https://pin.it/4k0R82Q") == "Pinterest"
    assert detect_media_platform("https://pinterest.de/pin/123456789012345678/") == "Pinterest"


def test_other_platforms_detection():
    assert detect_media_platform("https://twitter.com/user/status/123456789") == "X (Twitter)"
    assert detect_media_platform("https://x.com/user/status/123456789") == "X (Twitter)"
    assert detect_media_platform("https://www.reddit.com/r/videos/comments/123/cool_video/") == "Reddit"
    assert detect_media_platform("https://vimeo.com/123456789") == "Vimeo"
    assert detect_media_platform("https://example.com/video.mp4") == "Web Media"

