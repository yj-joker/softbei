def test_clarification_mode_defaults_to_enforce(monkeypatch):
    from config import settings as settings_module

    monkeypatch.delenv("CLARIFICATION_MODE", raising=False)
    settings_module._settings = None
    assert settings_module.get_settings().clarification_mode == "enforce"


def test_clarification_mode_accepts_only_supported_values(monkeypatch):
    from config import settings as settings_module

    monkeypatch.setenv("CLARIFICATION_MODE", "shadow")
    settings_module._settings = None
    assert settings_module.get_settings().clarification_mode == "shadow"

    monkeypatch.setenv("CLARIFICATION_MODE", "unexpected")
    settings_module._settings = None
    assert settings_module.get_settings().clarification_mode == "enforce"
