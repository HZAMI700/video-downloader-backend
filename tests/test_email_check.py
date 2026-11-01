import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings

client = TestClient(app)

def test_email_check_environment_variables():
    """Verify required Blackbird configuration variables upon startup."""
    assert hasattr(settings, "BLACKBIRD_ENABLED")
    assert hasattr(settings, "BLACKBIRD_TIMEOUT_SECONDS")
    assert hasattr(settings, "BLACKBIRD_RATE_LIMIT_PER_MINUTE")
    assert settings.BLACKBIRD_TIMEOUT_SECONDS > 0
    assert settings.BLACKBIRD_RATE_LIMIT_PER_MINUTE > 0


def test_email_check_health():
    """Health check endpoint should return 200 with service status."""
    response = client.get("/api/email-check/health")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "Blackbird OSINT Email Checker"
    assert "status" in data
    assert "blackbird_installed" in data
    assert "disclaimer" in data
    assert "rate_limit_per_minute" in data


def test_email_check_rejects_without_legal_consent():
    """Search must reject requests without mandatory legal acknowledgment."""
    response = client.post("/api/email-check", json={
        "email": "investigation@example.com",
        "legal_agreed": False
    })
    assert response.status_code == 400
    assert "legal disclaimer" in response.json().get("detail", "").lower()


def test_email_check_rejects_invalid_email_format():
    """Search must reject malformed email strings."""
    invalid_emails = [
        "not-an-email",
        "missingatsign.com",
        "user@",
        "@domain.com",
        "user@domain",
        "user space@domain.com"
    ]
    for bad_email in invalid_emails:
        response = client.post("/api/email-check", json={
            "email": bad_email,
            "legal_agreed": True
        })
        assert response.status_code == 400
        assert "invalid email format" in response.json().get("detail", "").lower()


def test_email_check_auth_rejection():
    """Search must reject requests when authentication fails."""
    # When configured token is set
    original_token = settings.BLACKBIRD_AUTH_TOKEN
    try:
        settings.BLACKBIRD_AUTH_TOKEN = "secret_blackbird_token_123"
        
        # Request with no auth
        response = client.post("/api/email-check", json={
            "email": "test@example.com",
            "legal_agreed": True
        })
        assert response.status_code == 401
        
        # Request with wrong auth
        response_wrong = client.post(
            "/api/email-check",
            json={"email": "test@example.com", "legal_agreed": True},
            headers={"Authorization": "Bearer wrong_token"}
        )
        assert response_wrong.status_code == 401
        
    finally:
        settings.BLACKBIRD_AUTH_TOKEN = original_token


def test_email_check_search_execution():
    """Successful search execution returns accounts and structured summary."""
    response = client.post("/api/email-check", json={
        "email": "test@example.com",
        "legal_agreed": True,
        "no_nsfw": True
    })
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "test@example.com"
    assert "accounts" in data
    assert "total_found" in data
    assert "summary" in data
    assert "disclaimer" in data
    assert isinstance(data["accounts"], list)


def test_email_check_ai_endpoint():
    """AI analysis endpoint should return structured footprint assessment."""
    response = client.post("/api/email-check/ai", json={
        "email": "test@example.com",
        "accounts": [
            {"platform": "Spotify", "category": "Music"},
            {"platform": "Twitter", "category": "Social"}
        ]
    })
    assert response.status_code == 200
    data = response.json()
    assert "analysis" in data
    assert "platforms_analyzed" in data
