"""The boot-time SSM load: fills only blank settings, never raises, and says
where the values came from."""

from __future__ import annotations

import pytest

from app.config import settings
from app.voice_platform import ssm_config


class FakeSSM:
    def __init__(self, pages: list[dict], *, fail: Exception | None = None) -> None:
        self.pages = pages
        self.fail = fail
        self.calls: list[dict] = []

    def get_parameters_by_path(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise self.fail
        index = int(kwargs.get("NextToken") or 0)
        page = dict(self.pages[index])
        if index + 1 < len(self.pages):
            page["NextToken"] = str(index + 1)
        return page


def _param(name: str, value: str) -> dict:
    return {"Name": f"/reep/voice-platform/{name}", "Value": value}


@pytest.fixture
def blank_platform(monkeypatch):
    for field in ssm_config.LOADABLE.values():
        monkeypatch.setattr(settings, field, "", raising=False)
    monkeypatch.setattr(settings, "platform_recordings_prefix", "recordings", raising=False)
    monkeypatch.setattr(ssm_config, "_source", "")
    monkeypatch.setenv("PLATFORM_SSM_PREFIX", "/reep/voice-platform")
    yield


def test_blank_settings_are_filled_and_set_values_are_kept(blank_platform, monkeypatch) -> None:
    monkeypatch.setattr(settings, "platform_ug_queue_url", "https://sqs/from-env", raising=False)
    client = FakeSSM([
        {"Parameters": [_param("PLATFORM_UG_QUEUE_URL", "https://sqs/from-ssm"), _param("PLATFORM_PG_QUEUE_URL", "https://sqs/pg")]},
        {"Parameters": [_param("PLATFORM_RECORDINGS_BUCKET", "reep-voice-recordings"), _param("NOT_A_SETTING", "x")]},
    ])
    applied = ssm_config.load(client)
    assert sorted(applied) == ["platform_pg_queue_url", "platform_recordings_bucket"]
    assert settings.platform_ug_queue_url == "https://sqs/from-env"  # environment wins
    assert settings.platform_pg_queue_url == "https://sqs/pg"
    assert settings.platform_recordings_bucket == "reep-voice-recordings"
    assert settings.platform_recordings_prefix == "recordings"  # non-blank default kept
    assert ssm_config.source() == "ssm:/reep/voice-platform"
    assert len(client.calls) == 2 and client.calls[0]["Path"] == "/reep/voice-platform"


def test_a_failing_client_leaves_everything_alone(blank_platform) -> None:
    client = FakeSSM([], fail=RuntimeError("AccessDeniedException"))
    assert ssm_config.load(client) == []
    assert settings.platform_ug_queue_url == ""
    assert ssm_config.source().startswith("unavailable: RuntimeError")


def test_nothing_is_attempted_outside_production_without_an_explicit_prefix(blank_platform, monkeypatch) -> None:
    monkeypatch.delenv("PLATFORM_SSM_PREFIX", raising=False)
    monkeypatch.setattr(type(settings), "is_prod", property(lambda self: False), raising=False)
    client = FakeSSM([{"Parameters": [_param("PLATFORM_PG_QUEUE_URL", "https://sqs/pg")]}])
    assert ssm_config.load(client) == []
    assert client.calls == [] and ssm_config.source() == "env"


def test_the_default_path_and_the_override(monkeypatch) -> None:
    monkeypatch.delenv("PLATFORM_SSM_PREFIX", raising=False)
    assert ssm_config.parameter_path() == "/reep/voice-platform"
    monkeypatch.setenv("PLATFORM_SSM_PREFIX", "college/voice/")
    assert ssm_config.parameter_path() == "/college/voice"
