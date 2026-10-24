"""The student-data egress gate — the AGENTS.md rule that student PII never
reaches an off-machine free model unless explicitly allowed. Pure logic, no DB.
"""

from app.ai.llm import is_loopback, student_data_egress_allowed
from app.config import settings


def test_is_loopback_true_for_local_hosts():
    assert is_loopback("http://127.0.0.1:11434/v1")
    assert is_loopback("http://localhost:1234/v1")
    assert is_loopback("http://[::1]:8080/v1")


def test_is_loopback_false_for_remote():
    assert not is_loopback("https://api.groq.com/openai/v1")
    assert not is_loopback("https://generativelanguage.googleapis.com/v1beta/openai")


def test_loopback_egress_always_allowed(monkeypatch):
    # Even with the remote flag off, a local model is always fine.
    monkeypatch.setattr(settings, "llm_allow_remote_student_data", "")
    assert student_data_egress_allowed("http://127.0.0.1:11434/v1") is True


def test_remote_egress_blocked_by_default(monkeypatch):
    monkeypatch.setattr(settings, "llm_allow_remote_student_data", "")
    assert student_data_egress_allowed("https://api.groq.com/openai/v1") is False


def test_remote_egress_needs_exact_true(monkeypatch):
    # Only the exact string "true" opens the gate (matches the Next.js gate).
    monkeypatch.setattr(settings, "llm_allow_remote_student_data", "yes")
    assert student_data_egress_allowed("https://api.groq.com/openai/v1") is False
    monkeypatch.setattr(settings, "llm_allow_remote_student_data", "true")
    assert student_data_egress_allowed("https://api.groq.com/openai/v1") is True
