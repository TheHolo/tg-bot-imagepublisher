from app.config import Settings


def test_comma_separated_admin_ids(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123456:abcdefghijklmnopqrstuvwxyzABCDEFGHI")
    monkeypatch.setenv("ADMIN_IDS", "123,456")
    assert Settings(_env_file=None).admin_ids == {123, 456}
