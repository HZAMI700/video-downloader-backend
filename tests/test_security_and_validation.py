import pytest
from fastapi import HTTPException
from app.security import validate_media_url, sanitize_filename, is_ip_private_or_restricted


def test_is_ip_private():
    assert is_ip_private_or_restricted("127.0.0.1") is True
    assert is_ip_private_or_restricted("10.0.0.1") is True
    assert is_ip_private_or_restricted("192.168.1.1") is True
    assert is_ip_private_or_restricted("172.16.0.1") is True
    assert is_ip_private_or_restricted("169.254.169.254") is True
    assert is_ip_private_or_restricted("::1") is True
    assert is_ip_private_or_restricted("8.8.8.8") is False
    assert is_ip_private_or_restricted("1.1.1.1") is False


def test_validate_url_rejections():
    # Empty
    with pytest.raises(HTTPException) as exc:
        validate_media_url("")
    assert exc.value.status_code == 400

    # Localhost
    with pytest.raises(HTTPException) as exc:
        validate_media_url("http://localhost:8000/test")
    assert exc.value.status_code == 400

    # Loopback IP
    with pytest.raises(HTTPException) as exc:
        validate_media_url("http://127.0.0.1:8000/test")
    assert exc.value.status_code == 400

    # Private IP
    with pytest.raises(HTTPException) as exc:
        validate_media_url("http://192.168.1.50/video.mp4")
    assert exc.value.status_code == 400

    # Cloud metadata endpoint
    with pytest.raises(HTTPException) as exc:
        validate_media_url("http://169.254.169.254/latest/meta-data/")
    assert exc.value.status_code == 400

    # Non-HTTP scheme
    with pytest.raises(HTTPException) as exc:
        validate_media_url("file:///etc/passwd")
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        validate_media_url("ftp://ftp.example.com/file")
    assert exc.value.status_code == 400


def test_validate_url_success():
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    validated = validate_media_url(url)
    assert validated == url


def test_sanitize_filename():
    assert sanitize_filename("../../etc/passwd.mp4") == "etc_passwd.mp4"
    assert sanitize_filename("video: test? *cool*.mp4") == "video_test_cool_.mp4"
    assert sanitize_filename("") == "media_download"
    assert sanitize_filename(None) == "media_download"
