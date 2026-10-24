import pytest
from starlette.testclient import TestClient
from unittest.mock import patch, AsyncMock
from app.main import app

client = TestClient(app)


def test_paraphrase_endpoint():
    with patch("app.routes.quillbot_ai.call_groq_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "AI is changing how we create and compose writing."
        res = client.post(
            "/api/ai/paraphrase",
            json={"text": "Artificial intelligence is changing how people write.", "mode": "standard", "synonyms": "medium"}
        )
        assert res.status_code == 200
        data = res.json()
        assert data["paraphrased_text"] == "AI is changing how we create and compose writing."
        assert data["mode"] == "standard"


def test_grammar_check_endpoint():
    with patch("app.routes.quillbot_ai.call_groq_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = '{"corrected_text": "She does not know anything about their plans.", "errors": [{"original": "dont", "suggestion": "does not", "type": "grammar", "explanation": "Subject-verb agreement"}]}'
        res = client.post(
            "/api/ai/grammar-check",
            json={"text": "She dont know nothing about their plans."}
        )
        assert res.status_code == 200
        data = res.json()
        assert "does not" in data["corrected_text"]
        assert len(data["errors"]) == 1


def test_summarize_endpoint():
    with patch("app.routes.quillbot_ai.call_groq_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "AI automation improves efficiency across multiple tasks."
        res = client.post(
            "/api/ai/summarize",
            json={
                "text": "Artificial intelligence automation significantly improves efficiency across everyday digital tasks, reducing redundant overhead and boosting productivity.",
                "format": "paragraph",
                "length": "short"
            }
        )
        assert res.status_code == 200
        data = res.json()
        assert data["summary_word_count"] > 0
        assert data["reduction_percent"] >= 0


def test_humanize_endpoint():
    with patch("app.routes.quillbot_ai.call_groq_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "Honestly, using smart software just makes life so much easier when writing."
        res = client.post(
            "/api/ai/humanize",
            json={"text": "It is undeniably vital to acknowledge the utility of modern algorithmic synthesis.", "tone": "casual"}
        )
        assert res.status_code == 200
        data = res.json()
        assert data["human_score"] >= 90


def test_chat_endpoint():
    with patch("app.routes.quillbot_ai.call_groq_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "Hello! I am QuillBot AI. How can I assist you today?"
        res = client.post(
            "/api/ai/chat",
            json={"messages": [{"role": "user", "content": "Hi there"}]}
        )
        assert res.status_code == 200
        data = res.json()
        assert "QuillBot" in data["reply"]


def test_plagiarism_endpoint():
    with patch("app.routes.quillbot_ai.call_groq_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = '{"originality_percent": 97, "matched_percent": 3, "scanned_sources": 180, "flagged_phrases": [], "summary": "Clean and original."}'
        res = client.post(
            "/api/ai/plagiarism-check",
            json={"text": "This is a completely unique essay written for testing plagiarism detection algorithms."}
        )
        assert res.status_code == 200
        data = res.json()
        assert data["originality_percent"] == 97
