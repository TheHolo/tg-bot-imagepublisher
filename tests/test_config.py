from app.config import Settings


def test_comma_separated_admin_ids(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123456:abcdefghijklmnopqrstuvwxyzABCDEFGHI")
    monkeypatch.setenv("ADMIN_IDS", "123,456")
    assert Settings(_env_file=None).admin_ids == {123, 456}


def test_repository_settings_are_loaded_from_toml_and_override_environment(tmp_path, monkeypatch):
    config = tmp_path / "bot-settings.toml"
    config.write_text(
        "[bot]\nauto_translate_titles = true\npixiv_max_images = 7\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTO_TRANSLATE_TITLES", "false")
    monkeypatch.setenv("PIXIV_MAX_IMAGES", "99")

    settings = Settings(
        _config_file=config, _env_file=None,
        bot_token="123456:abcdefghijklmnopqrstuvwxyzABCDEFGHI",
    )

    assert settings.auto_translate_titles is True
    assert settings.pixiv_max_images == 7


def test_repository_settings_reject_secrets(tmp_path):
    config = tmp_path / "bot-settings.toml"
    config.write_text('[bot]\nbot_token = "must-not-be-committed"\n', encoding="utf-8")

    try:
        Settings(
            _config_file=config, _env_file=None,
            bot_token="123456:abcdefghijklmnopqrstuvwxyzABCDEFGHI",
        )
    except ValueError as error:
        assert "секретные параметры" in str(error)
    else:
        raise AssertionError("A secret in bot-settings.toml must be rejected")


def test_env_file_accepts_single_quoted_channel_json(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join([
            "BOT_TOKEN=123456:abcdefghijklmnopqrstuvwxyzABCDEFGHI",
            "ADMIN_IDS=123456789",
            "DEFAULT_CHANNEL_ALIAS=artwork",
            'CHANNELS_JSON=\'{"artwork":{"chat_id":"-1001234567890","publish_mode":"auto"}}\'',
        ]),
        encoding="utf-8",
    )
    for name in ("BOT_TOKEN", "ADMIN_IDS", "DEFAULT_CHANNEL_ALIAS", "CHANNELS_JSON"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_config_file=tmp_path / "missing.toml", _env_file=env_file)

    assert settings.default_channel_alias == "artwork"
    assert settings.channels_json["artwork"]["chat_id"] == "-1001234567890"
