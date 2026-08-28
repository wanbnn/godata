from godata.config import Settings


def test_settings_loads_dotenv_from_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GODATA_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        'GODATA_API_KEY="' + "a" * 32 + '"\n',
        encoding="utf-8",
    )

    settings = Settings.from_env()

    assert settings.api_key == "a" * 32


def test_process_environment_takes_precedence_over_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GODATA_API_KEY", "b" * 32)
    monkeypatch.setenv("GODATA_ODBC_DRIVER", "Driver definido no processo")
    (tmp_path / ".env").write_text(
        'GODATA_API_KEY="' + "a" * 32 + '"\n'
        'GODATA_ODBC_DRIVER="Driver definido no arquivo"\n',
        encoding="utf-8",
    )

    settings = Settings.from_env()

    assert settings.api_key == "b" * 32
    assert settings.odbc_driver == "Driver definido no processo"


def test_zero_disables_query_timeout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GODATA_API_KEY", "a" * 32)
    monkeypatch.setenv("GODATA_QUERY_TIMEOUT_SECONDS", "0")

    settings = Settings.from_env()

    assert settings.query_timeout_seconds == 0
