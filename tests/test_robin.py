import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_robin_health():
    response = client.get("/api/robin/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "service" in data
    assert "tor" in data
    assert "opencode" in data
    assert "disclaimer" in data

def test_robin_search_without_legal_consent():
    response = client.post("/api/robin/search", json={
        "query": "CVE-2024-3094",
        "legal_agreed": False
    })
    assert response.status_code == 400

def test_robin_search_success():
    response = client.post("/api/robin/search", json={
        "query": "LockBit Ransomware",
        "preset": "threat_intel",
        "legal_agreed": True,
        "max_results": 10,
        "max_scrape": 3
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["query"] == "LockBit Ransomware"
    assert "investigation_id" in data
    assert "summary" in data
    assert len(data["sources"]) > 0
    assert len(data["pivots"]) > 0

    # Ensure every single onion link returned to the user is checked and verified online
    for src in data["sources"]:
        assert src["status"] == "online"
        assert src.get("is_verified") is True
        assert "latency_ms" in src
        assert src["latency_ms"] > 0
        assert "last_checked" in src
        assert src["link"].endswith(".onion") or ".onion/" in src["link"]

    # Test follow-up using investigation_id
    inv_id = data["investigation_id"]
    followup_resp = client.post("/api/robin/followup", json={
        "question": "What is the recommended incident response action?",
        "investigation_id": inv_id
    })
    assert followup_resp.status_code == 200
    fdata = followup_resp.json()
    assert "answer" in fdata
    assert len(fdata["answer"]) > 10

def test_robin_onion_verify_endpoint():
    # Test on-demand live checking of an individual onion link
    test_onion = "http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/search/?q=test"
    resp = client.post("/api/robin/verify-onion", json={"link": test_onion})
    assert resp.status_code == 200
    data = resp.json()
    assert data["link"] == test_onion
    assert data["status"] == "online"
    assert data["is_verified"] is True
    assert data["latency_ms"] > 0
    assert "last_checked" in data
