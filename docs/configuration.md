# Конфигурация

Настройки разделены на два файла:

- `.env` — секреты и параметры конкретного окружения;
- `bot-settings.toml` — несекретное поведение приложения, которое можно хранить в Git.

## Приоритет значений

Для обычного запуска приоритет такой:

1. значения, явно переданные конструктору `Settings` в коде;
2. секция `[bot]` или `[home_worker]` из `bot-settings.toml`;
3. переменные окружения и `.env`;
4. значения по умолчанию.

Поэтому несекретный параметр из TOML намеренно перекрывает одноимённую переменную окружения. Секретные поля не разрешены в TOML и всегда приходят из `.env`, окружения или явного аргумента.

Неизвестный ключ в TOML считается ошибкой. Это защищает от опечаток и случайного commit секретов.

## `.env` основного бота

| Переменная | Обязательность | Назначение |
| --- | --- | --- |
| `BOT_TOKEN` | обязательно | токен Telegram Bot API |
| `ADMIN_IDS` | обязательно | числовые Telegram ID через запятую |
| `DATABASE_URL` | нет | SQLAlchemy URL, по умолчанию локальная SQLite |
| `DEFAULT_CHANNEL_ALIAS` | обязательно при каналах | fallback alias основного канала |
| `CHANNELS_JSON` | обязательно | JSON-объект зарегистрированных каналов |
| `HTTP_PROXY` | нет | HTTP(S)-прокси |
| `SOCKS_PROXY` | нет | SOCKS4/5-прокси; имеет приоритет над HTTP |
| `PIXIV_COOKIES` | нет | cookies для закрытых работ Pixiv |
| `NEWS_WORKER_TOKEN` | нет | включает news API и создание новостей |
| `NEWS_API_BIND_HOST` | нет | отдельный bind host; нужен, например, внутри Docker |

Пример:

```env
BOT_TOKEN=123456:replace_with_real_token
ADMIN_IDS=123456789,987654321
DATABASE_URL=sqlite+aiosqlite:///./data/database.db
DEFAULT_CHANNEL_ALIAS=artwork
CHANNELS_JSON='{"artwork":{"chat_id":"-1001234567890","title":"Artwork","publish_mode":"auto"}}'
HTTP_PROXY=
SOCKS_PROXY=
PIXIV_COOKIES=
NEWS_WORKER_TOKEN=
```

## `CHANNELS_JSON`

Значение должно быть JSON-объектом на одной строке. Alias — ключ объекта:

```json
{
  "artwork": {
    "chat_id": "-1001234567890",
    "title": "Artwork",
    "publish_mode": "auto",
    "publish_interval_seconds": 3600,
    "enabled": true
  }
}
```

В `.env` этот JSON обычно заключается в одинарные кавычки.

| Поле канала | Значение |
| --- | --- |
| `chat_id` | обязательный Telegram chat ID |
| `title` | отображаемое название; по умолчанию alias |
| `publish_mode` | `auto`, `photo` или `document` |
| `publish_interval_seconds` | начальный интервал нового канала |
| `caption_template` | доверенный HTML-шаблон художественной подписи |
| `enabled` | доступность канала, по умолчанию `true` |

После создания интервал и основной канал сохраняются в базе и не перезаписываются TOML/JSON при каждом старте. Остальные перечисленные поля синхронизируются.

## Секция `[bot]`

| Параметр | По умолчанию | Ограничение и назначение |
| --- | ---: | --- |
| `storage_path` | `./storage` | папка временных и загруженных файлов |
| `log_level` | `INFO` | уровень Python logging |
| `worker_count` | `1` | от 1 до 8 публикационных workers |
| `max_job_attempts` | `3` | от 1 до 10 попыток |
| `download_timeout` | `60` | общий HTTP timeout, минимум 5 секунд |
| `max_download_size_mb` | `47` | от 1 до 47 MiB |
| `max_tags` | `20` | максимум итоговых тегов |
| `max_tag_length` | `64` | длина нормализованного тега |
| `max_urls_per_message` | `10` | от 1 до 50 ссылок в batch |
| `pixiv_media_limit_enabled` | `true` | ограничивать число страниц альбома |
| `pixiv_max_images` | `10` | от 1 до 1000 выбранных изображений |
| `auto_add_source_tags` | `true` | добавлять теги источника после пользовательских |
| `auto_translate_titles` | `true` | переводить не-ASCII названия на английский |
| `translation_timeout` | `5` | от 1 до 30 секунд |
| `delete_files_after_publish` | `true` | удалять каталог job после успеха |
| `files_ttl_hours` | `24` | зарезервирован; TTL-очистка пока не запускается |
| `timezone` | `UTC` | IANA timezone для ввода времени и дневных снимков |
| `news_api_host` | `127.0.0.1` | host news API |
| `news_api_port` | `8091` | порт от 1 до 65535 |
| `news_task_lease_seconds` | `1800` | lease от 60 до 21600 секунд |
| `news_model_name` | `gemma4:12b` | модель, которую должна объявить домашняя машина |

Пример проекта использует `Asia/Vladivostok`.

## Шаблон художественной подписи

`caption_template` может использовать:

- `{title}`;
- `{description_block}`;
- `{author_name}`;
- `{author_url}`;
- `{source_url}`;
- `{source_label}`;
- `{provider_name}`;
- `{published_at_block}`;
- `{hashtags}`;
- `{hashtags_block}`.

Данные источника экранируются, но сам шаблон считается доверенным HTML. Ошибка имени placeholder обнаружится при построении предпросмотра, поэтому проверяйте новый шаблон на тестовом посте.

## Домашний `.env`

Все поля имеют префикс `HOME_WORKER_`:

```env
HOME_WORKER_TOKEN=the_same_secret_as_news_worker_token
HOME_WORKER_VPS_API_URL=http://127.0.0.1:8091
HOME_WORKER_OLLAMA_BASE_URL=http://127.0.0.1:11434
HOME_WORKER_OLLAMA_MODEL=gemma4:12b
```

`HOME_WORKER_TOKEN` нельзя переносить в TOML.

## Секция `[home_worker]`

| Параметр | По умолчанию | Назначение |
| --- | ---: | --- |
| `vps_api_url` | `http://127.0.0.1:8091` | API VPS; удалённый адрес должен использовать HTTPS |
| `worker_id` | hostname | имя домашнего worker |
| `poll_interval_seconds` | `5` | пауза между пустыми poll |
| `lease_seconds` | `1800` | запрашиваемый lease, 60–7200 секунд |
| `request_timeout_seconds` | `30` | timeout VPS API |
| `max_retries` | `3` | число повторов VPS API |
| `retry_backoff_seconds` | `1` | начальный exponential backoff |
| `ollama_base_url` | `http://127.0.0.1:11434` | только loopback |
| `ollama_model` | `gemma4:12b` | локальная модель |
| `ollama_timeout_seconds` | `600` | timeout одного запроса модели |
| `ollama_max_retries` | `2` | повторы Ollama |
| `ollama_keep_alive` | `10m` | время удержания модели |
| `ollama_context_length` | `8192` | `num_ctx`, 2048–131072 |
| `ollama_max_predict_tokens` | `1600` | максимум ответа, 256–8192 |
| `temperature` | `0.1` | от 0 до 0.5 |
| `max_source_chars_per_chunk` | `24000` | первичный размер части источника |
| `max_source_chunks` | `16` | максимум частей, 1–64 |
| `source_types` | все четыре типа | `website`, `youtube`, `telegram`, `manual` |
| `log_level` | `INFO` | уровень логов worker |

Общий предел `max_source_chars_per_chunk * max_source_chunks` — 2 000 000 символов.

## База данных

SQLite:

```env
DATABASE_URL=sqlite+aiosqlite:///./data/database.db
```

PostgreSQL:

```env
DATABASE_URL=postgresql+asyncpg://user:password@host/database
```

Для PostgreSQL установите extra:

```bash
python -m pip install -e '.[postgres]'
```

Схема создаётся при старте. Для старых MVP-баз применяются встроенные additive migrations; полноценного миграционного фреймворка в проекте пока нет.
