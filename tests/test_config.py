import pytest

from godata.config import ConfigurationError, Settings, _targets


def test_target_allowlist_is_case_insensitive():
    settings = Settings(api_key="a" * 32, allowed_targets=_targets('{"SQL01":["ERP"]}'))
    assert settings.target_is_allowed("sql01", "erp")
    assert not settings.target_is_allowed("sql01", "financeiro")


def test_invalid_target_json_is_rejected():
    with pytest.raises(ConfigurationError):
        _targets("not-json")


def test_settings_loads_dotenv_from_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GODATA_API_KEY", raising=False)
    monkeypatch.delenv("GODATA_ALLOWED_TARGETS", raising=False)
    (tmp_path / ".env").write_text(
        'GODATA_API_KEY="' + "a" * 32 + '"\n'
        'GODATA_ALLOWED_TARGETS={"sql01":["ERP"]}\n',
        encoding="utf-8",
    )

    settings = Settings.from_env()

    assert settings.api_key == "a" * 32
    assert settings.target_is_allowed("sql01", "erp")


def test_process_environment_takes_precedence_over_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GODATA_API_KEY", "b" * 32)
    monkeypatch.setenv("GODATA_ALLOWED_TARGETS", '{"process-server":["ERP"]}')
    (tmp_path / ".env").write_text(
        'GODATA_API_KEY="' + "a" * 32 + '"\n'
        'GODATA_ALLOWED_TARGETS={"file-server":["ERP"]}\n',
        encoding="utf-8",
    )

    settings = Settings.from_env()

    assert settings.api_key == "b" * 32
    assert settings.target_is_allowed("process-server", "ERP")
    assert not settings.target_is_allowed("file-server", "ERP")
