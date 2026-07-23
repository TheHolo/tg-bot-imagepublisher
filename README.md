# Telegram Image Publisher

Приватный Telegram-бот для получения оригинальных изображений из Pixiv, DeviantArt и по прямым ссылкам, предпросмотра метаданных и публикации в настроенные Telegram-каналы. Очередь хранится в базе данных и восстанавливается после перезапуска приложения.

## Возможности

- доступ только для Telegram ID из `ADMIN_IDS`;
- Pixiv, DeviantArt и прямые ссылки на JPG, PNG, WebP и GIF;
- несколько ссылок в одном сообщении, пользовательские теги и выбор целевого канала;
- предпросмотр, подтверждение, отмена, ручная публикация и повтор неудачного задания;
- независимая очередь и интервал публикации для каждого канала;
- дедупликация публикаций по источнику и каналу;
- проверка формата, размера и количества пикселей изображения;
- отправка фото, документов и однородных медиагрупп до 10 элементов;
- автоматическое восстановление прерванных загрузок и обработки после перезапуска;
- запуск через systemd или Docker Compose.

## Требования

- Linux-сервер; инструкция systemd ниже рассчитана на Ubuntu 24.04;
- Python 3.12 или новее;
- исходящий HTTPS-доступ к Telegram API и используемым источникам;
- Telegram-бот, созданный через [@BotFather](https://t.me/BotFather);
- числовые Telegram ID администраторов;
- бот добавлен администратором в каждый целевой канал и может публиковать сообщения.

Бот использует long polling, поэтому открывать входящий сетевой порт не требуется. Не запускайте одновременно systemd- и Docker-экземпляры с одним `BOT_TOKEN`.

## Подготовка Telegram

1. Создайте бота через `@BotFather` и сохраните токен.
2. Узнайте числовые Telegram ID пользователей, которым будет разрешено управление ботом. Имена пользователей вида `@name` не подходят.
3. Добавьте бота администратором в целевой канал.
4. Разрешите ему публикацию сообщений.
5. Узнайте числовой chat ID канала. Для каналов он обычно начинается с `-100`.

Токен, cookies и другие секреты должны храниться только в `.env`. Не отправляйте их в сообщения и не добавляйте в Git.

## Установка на сервер с нуля через systemd

Все файлы приложения должны находиться непосредственно в `/opt/telegram-image-publisher`:

```text
/opt/telegram-image-publisher/
├── .git/
├── .venv/
├── .env
├── app/
├── data/
├── deploy/
├── storage/
├── bot-settings.toml
└── pyproject.toml
```

Production-ветка — `main`. Команды ниже следует выполнять под пользователем с правами `sudo`.

### 1. Установите системные пакеты

```bash
sudo apt update
sudo apt install -y ca-certificates git python3.12 python3.12-venv util-linux
python3.12 --version
```

Версия в последней команде должна быть не ниже 3.12. На другом дистрибутиве установите Python 3.12+, модуль `venv`, Git и `flock` штатным пакетным менеджером.

### 2. Создайте системного пользователя и получите код

Задайте `REPOSITORY_URL` как HTTPS- или SSH-адрес репозитория. Для приватного репозитория заранее настройте deploy key или другой способ чтения Git под пользователем `telegram-publisher`.

```bash
REPOSITORY_URL='REPLACE_WITH_REPOSITORY_URL'

sudo useradd --system --user-group \
  --home-dir /opt/telegram-image-publisher \
  --shell /usr/sbin/nologin \
  telegram-publisher

sudo install -d -o telegram-publisher -g telegram-publisher -m 0750 \
  /opt/telegram-image-publisher

sudo -u telegram-publisher git clone \
  --branch main --single-branch \
  "$REPOSITORY_URL" \
  /opt/telegram-image-publisher

cd /opt/telegram-image-publisher
```

Если пользователь `telegram-publisher` уже существует, команду `useradd` выполнять повторно не нужно. Каталог назначения перед `git clone` должен быть пустым.

### 3. Создайте виртуальное окружение и установите зависимости

```bash
sudo -u telegram-publisher python3.12 -m venv \
  /opt/telegram-image-publisher/.venv

sudo -u telegram-publisher \
  /opt/telegram-image-publisher/.venv/bin/python -m pip install --upgrade pip

sudo -u telegram-publisher \
  /opt/telegram-image-publisher/.venv/bin/python -m pip install -e \
  /opt/telegram-image-publisher
```

Для SOCKS-прокси вместо последней команды установите extra `proxy`:

```bash
sudo -u telegram-publisher \
  /opt/telegram-image-publisher/.venv/bin/python -m pip install -e \
  '/opt/telegram-image-publisher[proxy]'
```

Для PostgreSQL используйте extra `postgres`; extras можно объединять как `[proxy,postgres]`.

При PostgreSQL также замените `DATABASE_URL`, например на `postgresql+asyncpg://user:password@database-host/database-name`. Скрипт обновления автоматически копирует только локальную SQLite-базу; резервное копирование PostgreSQL нужно настроить отдельно.

### 4. Настройте `.env`

```bash
sudo -u telegram-publisher cp \
  /opt/telegram-image-publisher/.env.example \
  /opt/telegram-image-publisher/.env

sudo chmod 0600 /opt/telegram-image-publisher/.env
sudoedit /opt/telegram-image-publisher/.env
```

Минимальная конфигурация:

```env
BOT_TOKEN=123456:replace_with_real_token
ADMIN_IDS=123456789
DATABASE_URL=sqlite+aiosqlite:///./data/database.db
DEFAULT_CHANNEL_ALIAS=artwork
CHANNELS_JSON='{"artwork":{"chat_id":"-1001234567890","title":"Artwork","publish_mode":"auto"}}'
HTTP_PROXY=
SOCKS_PROXY=
PIXIV_COOKIES=
```

Правила заполнения:

- `ADMIN_IDS` — один или несколько числовых ID через запятую;
- `DEFAULT_CHANNEL_ALIAS` должен совпадать с одним из ключей `CHANNELS_JSON`;
- `CHANNELS_JSON` должен находиться на одной строке, содержать корректный JSON и быть целиком заключён в одинарные кавычки;
- `chat_id` рекомендуется записывать строкой, чтобы знак минус сохранился без преобразований;
- `publish_mode` принимает `auto`, `photo` или `document`;
- `publish_interval_seconds` задаёт минимальный интервал канала в секундах и по умолчанию равен `0`;
- `enabled` позволяет временно отключить канал;
- `HTTP_PROXY` и `SOCKS_PROXY` необязательны; при наличии обоих используется SOCKS;
- `PIXIV_COOKIES` нужен только для работ, доступных авторизованному пользователю Pixiv.

Пример нескольких каналов:

```env
DEFAULT_CHANNEL_ALIAS=artwork
CHANNELS_JSON='{"artwork":{"chat_id":"-1001234567890","title":"Artwork","publish_mode":"auto","publish_interval_seconds":3600},"archive":{"chat_id":"-1009876543210","title":"Archive","publish_mode":"document","enabled":true}}'
```

Обычные настройки поведения находятся в `bot-settings.toml`. Этот файл хранится в Git и не должен содержать токены, cookies или другие секреты. Значения из его секции `[bot]` намеренно имеют приоритет над одноимёнными переменными окружения.

Основные настройки `bot-settings.toml`:

| Параметр | Назначение |
| --- | --- |
| `worker_count` | Число параллельных worker, от 1 до 8. Для одного канала одновременно обрабатывается не более одного задания. |
| `max_job_attempts` | Максимум попыток для повторяемых ошибок, от 1 до 10. |
| `download_timeout` | Общий тайм-аут сетевой операции в секундах, минимум 5. |
| `max_download_size_mb` | Лимит входного файла, от 1 до 47 МиБ. |
| `max_urls_per_message` | Максимум ссылок в одном сообщении, от 1 до 50. |
| `auto_add_source_tags` | Добавлять теги источника после пользовательских тегов. |
| `auto_translate_titles` | Переводить не-ASCII заголовки через внешний сервис MyMemory. При ошибке используется оригинал. |
| `pixiv_media_limit_enabled` | Проверять число изображений Pixiv до скачивания. |
| `pixiv_max_images` | Допустимое число изображений одной Pixiv-публикации. |
| `delete_files_after_publish` | Удалять файлы задания после успешной публикации. |

### 5. Проверьте конфигурацию

Команда проверяет загрузку `.env` и `bot-settings.toml`, не выводя секреты:

```bash
sudo -u telegram-publisher bash -c \
  'cd /opt/telegram-image-publisher && .venv/bin/python -c "from app.config import Settings; s = Settings(); assert s.admin_ids, \"ADMIN_IDS is empty\"; assert s.default_channel_alias in s.channels_json, \"DEFAULT_CHANNEL_ALIAS is missing in CHANNELS_JSON\"; print(\"Configuration OK:\", len(s.admin_ids), \"admin(s),\", len(s.channels_json), \"channel(s)\")"'
```

Создавать `data` и `storage` вручную не обязательно: приложение создаст их при первом запуске. Владелец каталога приложения должен оставаться `telegram-publisher`.

### 6. Установите и запустите systemd unit

```bash
cd /opt/telegram-image-publisher

sudo install -m 0644 \
  deploy/telegram-image-publisher.service \
  /etc/systemd/system/telegram-image-publisher.service

sudo systemctl daemon-reload
sudo systemctl enable --now telegram-image-publisher
```

Проверьте состояние и журнал:

```bash
sudo systemctl status telegram-image-publisher --no-pager
sudo journalctl -u telegram-image-publisher -n 100 --no-pager
sudo journalctl -u telegram-image-publisher -f
```

После успешного запуска отправьте боту `/menu`, затем `/health` и `/channels`. `/menu` впервые устанавливает постоянную клавиатуру в этом чате. Если бот не отвечает, сначала проверьте журнал systemd, `BOT_TOKEN` и наличие вашего ID в `ADMIN_IDS`.

## Обновление systemd-установки

Скрипт `deploy/update.sh`:

- проверяет каталог приложения и ветку `main`;
- запрещает обновление при локальных изменениях tracked-файлов;
- получает `origin/main` и применяет только fast-forward;
- останавливает приложение и перед обновлением кода создаёт резервную копию SQLite в `data/backups`;
- обновляет Python-зависимости, проверяет компиляцию и запускает сервис;
- проверяет, что сервис не упал сразу после запуска.

Запуск обновления:

```bash
cd /opt/telegram-image-publisher
sudo ./deploy/update.sh
```

Если executable-флаг скрипта потерян:

```bash
cd /opt/telegram-image-publisher
sudo bash deploy/update.sh
```

Для приватного репозитория команда `git fetch` должна работать без интерактивного ввода от имени `telegram-publisher`. Проверить это можно так:

```bash
sudo -H -u telegram-publisher git -C /opt/telegram-image-publisher fetch origin main
```

Если изменился systemd unit, после обновления повторно скопируйте его и перечитайте конфигурацию:

```bash
cd /opt/telegram-image-publisher
sudo install -m 0644 deploy/telegram-image-publisher.service \
  /etc/systemd/system/telegram-image-publisher.service
sudo systemctl daemon-reload
sudo systemctl restart telegram-image-publisher
```

## Ручной запуск для разработки

```bash
REPOSITORY_URL='REPLACE_WITH_REPOSITORY_URL'
git clone --branch main "$REPOSITORY_URL" telegram-image-publisher
cd telegram-image-publisher
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cp .env.example .env
# заполните .env
python -m app.main
```

При первом старте автоматически создаются каталоги данных, таблицы базы данных, необходимые добавочные изменения схемы и каналы из `CHANNELS_JSON`.

## Docker Compose

Установите Docker Engine с Compose plugin, затем подготовьте репозиторий и `.env`:

```bash
REPOSITORY_URL='REPLACE_WITH_REPOSITORY_URL'
git clone --branch main "$REPOSITORY_URL" telegram-image-publisher
cd telegram-image-publisher
cp .env.example .env
# заполните .env
docker compose up -d --build
docker compose logs -f bot
```

SQLite и временные файлы сохраняются в именованных volumes `bot-data` и `bot-storage`. Фактические имена можно увидеть командой:

```bash
docker compose config --volumes
docker volume ls
```

Файл `.dockerignore` исключает `.env`, Git-метаданные, локальную базу и storage из контекста сборки.

Перед обновлением остановите контейнер и сохраните volume с базой данных, указав его фактическое имя в `DATA_VOLUME`:

```bash
DATA_VOLUME='REPLACE_WITH_VOLUME_NAME'
docker compose stop bot
docker run --rm \
  -v "${DATA_VOLUME}:/data:ro" \
  -v "$PWD:/backup" \
  alpine sh -c 'tar czf /backup/bot-data-backup.tgz -C /data .'
git pull --ff-only origin main
docker compose up -d --build
```

Не запускайте для одной установки одновременно Docker Compose и systemd.

## Использование

Пример сообщения:

```text
https://www.pixiv.net/en/artworks/147382169 art landscape --channel artwork
```

По умолчанию можно отправить до 10 ссылок в одном сообщении, разделяя их пробелами, запятыми или переносами строк. Лимит задаётся параметром `max_urls_per_message`. Теги и `--channel` применяются ко всем ссылкам; для каждой публикации бот показывает отдельное подтверждение.

Без `--channel` используется последний канал, выбранный этим администратором. При первом использовании применяется `DEFAULT_CHANNEL_ALIAS`.

### Главное меню

`/start` и `/menu` показывают постоянную клавиатуру управления. После первого вызова она остаётся доступна через значок клавиатуры справа от поля ввода Telegram. Кнопки главного меню:

- `📋 Очередь` — выбор общей очереди или очереди активного канала;
- `🖼 Следующий пост` — выбор ближайшего поста во всей очереди или в активном канале;
- `📊 Статистика` — текущая статистика заданий;
- `📡 Каналы` — список всех зарегистрированных, включая отключённые, каналов;
- `🩺 Здоровье` — быстрый health-check с кнопками обновления и полной проверки;
- `ℹ️ Помощь` — актуальная памятка по slash-командам и их аргументам.

Список активных каналов в подменю очереди и предпросмотра строится из текущего состояния БД. Главное меню не заменяет текстовый ввод: ссылку на публикацию по-прежнему можно отправить обычным сообщением.

Команды:

- `/start`, `/menu` — открыть главное меню;
- `/help` — полная памятка по командам и их опциям;
- `/status <id>` — состояние задания;
- `/queue [alias]` — общая очередь или очередь канала;
- `/preview [id|alias]` — предпросмотр queued-задания без изменения очереди;
- `/publish [id]` — приоритетная публикация queued-задания;
- `/cancel <id>` — отмена;
- `/retry <id>` — повтор доступного для повтора задания;
- `/recent` — последние задания;
- `/channels` — настроенные каналы и интервалы;
- `/channel_interval <alias> <30s|15m|2h|1d|0>` — изменение интервала канала;
- `/providers` — список источников;
- `/stats` — статистика;
- `/health [full]` — быстрый или расширенный health-check приложения.

Команды и callback-кнопки доступны только пользователям из `ADMIN_IDS`.

### Health-check

`/health` выполняет быстрые проверки без записи файлов и без запросов к сайтам-источникам:

- доступность и задержка базы данных;
- число живых и занятых worker;
- текущий job, стадия, канал и длительность работы каждого занятого worker;
- размер очереди, возраст старейшего queued-задания и зависшие активные задания;
- последняя успешная публикация;
- ошибки и `uncertain_publish` за последние 24 часа;
- число активных каналов, default-канал и согласованность channel leases;
- доступность storage для записи и свободное место;
- доступность и задержка Telegram Bot API.

Пример:

```text
🟢 Bot healthy · uptime 3d 4h · проверка 84 мс

Database     🟢 OK · 5 мс
Workers      🟢 2/2 active · занято 1
  └ worker-0 · #152 downloading 1/3 · artwork · 18s
Queue        🟡 14 queued · oldest 42m
Publications 🟢 last success 7m ago · artwork
Failures     🟡 2 за 24h · uncertain: 0
Channels     🟢 3 enabled · leases OK
Storage      🟢 writable · свободно 18.4 GB
Telegram     🟢 API 63 мс
```

Idle-worker отдельной строкой не выводятся. Жёлтая строка означает предупреждение или наличие накопленной работы, но не делает весь процесс нездоровым. Красная строка означает, что одна из основных функций недоступна; заголовок при этом меняется на `Bot unhealthy`.

`/health full` дополнительно:

- показывает текущий размер SQLite или PostgreSQL в `KB`, `MB` или `GB`;
- выводит время последней успешной публикации отдельно для каждого активного канала;
- через Telegram API проверяет право бота публиковать в каждом активном канале;
- создаёт, синхронизирует и удаляет временный файл в storage;
- проверяет доступность Pixiv и DeviantArt с индивидуальным тайм-аутом.

Расширенная проверка может выполняться несколько секунд. Ошибка внешнего provider отображается предупреждением: она не означает, что БД, Telegram polling или worker самого бота не работают.

## Ограничения публикации и восстановление

- максимальный размер загружаемого файла — 47 МиБ;
- безопасный предел документа перед отправкой — 49 МБ;
- медиагруппа содержит не более 10 элементов;
- если один элемент альбома требуется отправить документом, весь альбом отправляется однородными группами документов;
- остаток из одного элемента отправляется отдельным сообщением;
- Pixiv-публикации с числом изображений больше `pixiv_max_images` по умолчанию отклоняются до скачивания;
- настройка `pixiv_media_limit_enabled = false` отключает это ограничение;
- прерванные стадии загрузки и обработки возвращаются в очередь при старте;
- неопределённый результат стадии `publishing` получает ошибку `uncertain_publish` и не повторяется автоматически — сначала проверьте канал, затем при необходимости используйте ручной retry.

## Тесты

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest -q
```

Unit- и интеграционные тесты не выполняют реальные сетевые запросы.

## Диагностика

### Бот не запускается

```bash
sudo systemctl status telegram-image-publisher --no-pager
sudo journalctl -u telegram-image-publisher -n 100 --no-pager
```

Проверьте синтаксис `.env`, наличие `BOT_TOKEN`, непустой `ADMIN_IDS`, корректный JSON в `CHANNELS_JSON` и совпадение `DEFAULT_CHANNEL_ALIAS` с одним из каналов.

### Бот не публикует в канал

Проверьте `chat_id`, наличие бота среди администраторов канала и право публикации сообщений. Для альбомов бот также должен иметь возможность отправлять медиа и документы.

### Не работает Pixiv или DeviantArt

Проверьте исходящий HTTPS-доступ, ответы 403/429, актуальность `PIXIV_COOKIES` и настройки прокси. Бот не обходит приватность, DRM и ограничения источника.

### Не работает SOCKS-прокси

Убедитесь, что установлен extra `proxy`:

```bash
sudo -u telegram-publisher \
  /opt/telegram-image-publisher/.venv/bin/python -m pip install -e \
  '/opt/telegram-image-publisher[proxy]'
sudo systemctl restart telegram-image-publisher
```

## Добавление provider

1. Реализуйте `BaseProvider` в `app/providers/`.
2. Возвращайте унифицированные `SourcePost` и `MediaItem`, не вызывая Telegram API.
3. Зарегистрируйте provider в `app/bootstrap.py`.
4. Добавьте тесты URL, нормализации, ошибок доступа, timeout/rate-limit и нескольких файлов.

Downloader повторно проверяет публичный DNS, запрещает redirect при загрузке и ограничивает размер. Это часть SSRF-защиты: прямые ссылки с перенаправлением намеренно не принимаются.
