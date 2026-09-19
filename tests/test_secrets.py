from unittest import mock

import pytest

from modules import secrets


def _kc(returncode=0, stdout=""):
    return mock.Mock(returncode=returncode, stdout=stdout, stderr="")


def test_get_kis_credentials_reads_key_and_secret_from_one_item():
    """acct에 app_key, 비밀번호에 app_secret — 쌍으로 발급되는 키를 항목 하나에 담는다."""
    attrs = _kc(stdout='    "acct"<blob>="APPKEY123"\n    "svce"<blob>="wst-kis"\n')
    pw = _kc(stdout="APPSECRET456\n")
    with mock.patch.object(secrets.subprocess, "run", side_effect=[attrs, pw]) as run:
        assert secrets.get_kis_credentials() == ("APPKEY123", "APPSECRET456")
        assert run.call_args_list[0][0][0][:2] == ["security", "find-generic-password"]


def test_get_kis_credentials_raises_with_registration_hint_when_missing():
    """등록 명령을 에러에 담는다 — notifier.py 패턴. 폴백이 없으므로 하드 페일."""
    with mock.patch.object(secrets.subprocess, "run", return_value=_kc(returncode=44)):
        with pytest.raises(RuntimeError) as ei:
            secrets.get_kis_credentials()
        assert "add-generic-password" in str(ei.value)
        assert "wst-kis" in str(ei.value)


def test_get_ai_api_key_uses_service_named_for_provider():
    with mock.patch.object(secrets.subprocess, "run",
                           return_value=_kc(stdout="ANTHROPICKEY\n")) as run:
        assert secrets.get_ai_api_key("anthropic") == "ANTHROPICKEY"
        assert "wst-anthropic" in run.call_args[0][0]


def test_get_ai_api_key_rejects_unknown_provider():
    with pytest.raises(RuntimeError) as ei:
        secrets.get_ai_api_key("nosuchprovider")
    assert "nosuchprovider" in str(ei.value)


def test_resolve_injects_kis_and_declared_provider_key():
    config = {"kis": {"account_no": "123"}, "ai": {"provider": "anthropic"}}
    with mock.patch.object(secrets, "get_kis_credentials", return_value=("K", "S")), \
         mock.patch.object(secrets, "get_ai_api_key", return_value="A") as ai:
        out = secrets.resolve(config)
    assert out["kis"]["app_key"] == "K"
    assert out["kis"]["app_secret"] == "S"
    assert out["ai"]["api_key"] == "A"
    assert ai.call_args[0][0] == "anthropic"
    assert out["kis"]["account_no"] == "123"


def test_resolve_asks_only_for_the_declared_provider():
    """provider를 바꿔도 다른 provider의 키를 요구하지 않는다."""
    config = {"kis": {}, "ai": {"provider": "openai"}}
    with mock.patch.object(secrets, "get_kis_credentials", return_value=("K", "S")), \
         mock.patch.object(secrets, "get_ai_api_key", return_value="O") as ai:
        secrets.resolve(config)
    assert ai.call_count == 1
    assert ai.call_args[0][0] == "openai"


def test_load_config_resolves_secrets_from_keychain(tmp_path):
    """평문 키가 없는 config.yaml로도 파이프라인이 키를 얻는다."""
    import main

    cfg = tmp_path / "config.yaml"
    cfg.write_text("kis:\n  account_no: '123'\nai:\n  provider: anthropic\n", encoding="utf-8")
    with mock.patch.object(secrets, "get_kis_credentials", return_value=("K", "S")), \
         mock.patch.object(secrets, "get_ai_api_key", return_value="A"):
        out = main.load_config(str(cfg))
    assert out["kis"]["app_key"] == "K"
    assert out["kis"]["app_secret"] == "S"
    assert out["ai"]["api_key"] == "A"


def test_regen_report_load_config_resolves_secrets_too(tmp_path):
    """regen_report는 reporter.generate_report를 호출하므로 AI 키가 필요하다."""
    import regen_report

    cfg = tmp_path / "config.yaml"
    cfg.write_text("kis: {}\nai:\n  provider: anthropic\n", encoding="utf-8")
    with mock.patch.object(secrets, "get_kis_credentials", return_value=("K", "S")), \
         mock.patch.object(secrets, "get_ai_api_key", return_value="A"):
        out = regen_report.load_config(str(cfg))
    assert out["ai"]["api_key"] == "A"


def test_resolve_does_not_mutate_caller_config():
    config = {"kis": {}, "ai": {"provider": "anthropic"}}
    with mock.patch.object(secrets, "get_kis_credentials", return_value=("K", "S")), \
         mock.patch.object(secrets, "get_ai_api_key", return_value="A"):
        secrets.resolve(config)
    assert "app_key" not in config["kis"]
    assert "api_key" not in config["ai"]
