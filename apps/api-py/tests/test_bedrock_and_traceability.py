"""Bedrock (Nova) adapter resolution + the request-traceability contract.

Pure logic — no AWS credentials, no database: boto3 is never actually called
(the client factory is monkeypatched), and the middleware tests ride any
existing endpoint through TestClient.
"""

from app.ai import llm
from app.ai.llm import LLMConfig, StudentDataEgressRefused, llm_config


def test_bedrock_resolution_order(monkeypatch):
    """BEDROCK_MODEL alone selects bedrock; the explicit LLM_* trio still wins;
    a stray free-tier key does NOT route around Bedrock."""
    monkeypatch.setattr(llm.settings, "llm_base_url", "")
    monkeypatch.setattr(llm.settings, "llm_model", "")
    monkeypatch.setattr(llm.settings, "llm_api_key", "")
    monkeypatch.setattr(llm.settings, "groq_api_key", "stray-free-key")
    monkeypatch.setattr(llm.settings, "bedrock_model", "apac.amazon.nova-pro-v1:0")

    cfg = llm_config()
    assert cfg is not None
    assert cfg.provider == "bedrock"
    assert cfg.model == "apac.amazon.nova-pro-v1:0"

    # Explicit trio wins over bedrock.
    monkeypatch.setattr(llm.settings, "llm_base_url", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(llm.settings, "llm_model", "llama3.2:3b")
    assert llm_config().provider == "custom"


def test_bedrock_is_off_machine_for_rule_1(monkeypatch):
    """Rule 1: Bedrock is remote — student data still needs the explicit flag."""
    monkeypatch.setattr(llm.settings, "llm_base_url", "")
    monkeypatch.setattr(llm.settings, "llm_model", "")
    monkeypatch.setattr(llm.settings, "llm_api_key", "")
    monkeypatch.setattr(llm.settings, "bedrock_model", "apac.amazon.nova-lite-v1:0")
    monkeypatch.setattr(llm.settings, "llm_allow_remote_student_data", "")
    try:
        llm.complete_chat([{"role": "user", "content": "hi"}], carries_student_data=True)
        assert False, "egress gate did not fire"
    except StudentDataEgressRefused:
        pass


def test_bedrock_payload_mapping_and_transport(monkeypatch):
    """OpenAI-shaped messages map onto converse; json_mode becomes a system
    instruction; the text comes back joined."""
    captured = {}

    class FakeClient:
        def converse(self, modelId, **payload):
            captured["modelId"] = modelId
            captured["payload"] = payload
            return {"output": {"message": {"content": [{"text": "hello "}, {"text": "nova"}]}}}

    monkeypatch.setattr(llm, "_bedrock_client", lambda cfg: FakeClient())
    cfg = LLMConfig("bedrock", "apac.amazon.nova-pro-v1:0", "", 30.0, provider="bedrock")
    out = llm._bedrock_complete(
        cfg,
        [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ],
        temperature=0.1,
        json_mode=True,
        max_tokens=64,
    )
    assert out == "hello nova"
    assert captured["modelId"] == "apac.amazon.nova-pro-v1:0"
    payload = captured["payload"]
    assert payload["messages"] == [{"role": "user", "content": [{"text": "hi"}]}]
    system_texts = [s["text"] for s in payload["system"]]
    assert "be brief" in system_texts
    assert any("JSON" in s for s in system_texts)
    assert payload["inferenceConfig"] == {"temperature": 0.1, "maxTokens": 64}


def test_every_response_carries_a_request_id(client):
    r = client.get("/api/auth/me")  # 401 is fine — the trace must ride failures too
    rid = r.headers.get("X-Request-ID")
    assert rid and len(rid) == 32  # generated uuid4 hex


def test_callers_own_request_id_is_kept_and_sanitised(client):
    r = client.get("/api/auth/me", headers={"X-Request-ID": "edge-42.trace"})
    assert r.headers.get("X-Request-ID") == "edge-42.trace"
    # Anything outside [A-Za-z0-9._-] is stripped, never echoed verbatim.
    r = client.get("/api/auth/me", headers={"X-Request-ID": "abc<script>!$%42"})
    assert r.headers.get("X-Request-ID") == "abcscript42"
