import pytest
from pydantic import ValidationError

from app.news.worker_config import HomeWorkerSettings


def test_home_worker_loads_non_secret_toml_and_secret_env(tmp_path, monkeypatch):
    config = tmp_path / "worker.toml"
    config.write_text(
        """[home_worker]
vps_api_url = "https://publisher.example"
ollama_model = "gemma4:12b"
temperature = 0.05
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME_WORKER_TOKEN", "worker-secret")

    settings = HomeWorkerSettings(_config_file=config, _env_file=None)

    assert settings.vps_api_url == "https://publisher.example"
    assert settings.ollama_model == "gemma4:12b"
    assert settings.temperature == 0.05
    assert settings.token.get_secret_value() == "worker-secret"


def test_home_worker_rejects_token_in_toml(tmp_path):
    config = tmp_path / "worker.toml"
    config.write_text('[home_worker]\ntoken = "must-not-be-committed"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="HOME_WORKER_TOKEN"):
        HomeWorkerSettings(_config_file=config, _env_file=None)


def test_home_worker_rejects_network_exposed_ollama():
    with pytest.raises(ValidationError, match="never expose Ollama"):
        HomeWorkerSettings(
            _config_file="missing.toml",
            _env_file=None,
            token="secret",
            vps_api_url="https://publisher.example",
            ollama_base_url="http://192.168.1.5:11434",
        )


def test_home_worker_requires_https_for_remote_vps():
    with pytest.raises(ValidationError, match="must use HTTPS"):
        HomeWorkerSettings(
            _config_file="missing.toml",
            _env_file=None,
            token="secret",
            vps_api_url="http://publisher.example",
        )
