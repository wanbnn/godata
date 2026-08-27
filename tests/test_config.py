import pytest

from godata.config import ConfigurationError, Settings, _targets


def test_target_allowlist_is_case_insensitive():
    settings = Settings(api_key="a" * 32, allowed_targets=_targets('{"SQL01":["ERP"]}'))
    assert settings.target_is_allowed("sql01", "erp")
    assert not settings.target_is_allowed("sql01", "financeiro")


def test_invalid_target_json_is_rejected():
    with pytest.raises(ConfigurationError):
        _targets("not-json")
