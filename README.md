# Telegram Image & News Publisher

Приватный Telegram-бот для подготовки и публикации изображений и новостей. Он принимает источник, создаёт редактируемый черновик, ставит его в очередь и публикует в выбранный канал.

Новости переписываются локально через Ollama на домашнем компьютере. VPS хранит очередь, показывает интерфейс и отправляет готовые публикации в Telegram.

## Что умеет бот

| Возможность | Кратко | Подробно |
| --- | --- | --- |
| Публикации с изображениями | Pixiv, DeviantArt и прямые ссылки на изображения | [Публикации с изображениями](docs/artwork-publications.md) |
| Новости | Сайты, YouTube, публичные `t.me`-посты, пересылки и ручной текст | [Новости и домашний AI-worker](docs/news-publications.md) |
| Предпросмотр и редактор | Изменение текста, тегов, подписи, канала, времени и медиа | [Предпросмотр и редактирование](docs/preview-and-editing.md) |
| Очередь | Отдельный порядок и интервал для каждого канала, точное время, пауза и ручной запуск | [Очередь и расписание](docs/queue-and-scheduling.md) |
| Каналы и подписчики | Управление каналами, права бота и история числа подписчиков | [Каналы и статистика](docs/channels-and-statistics.md) |
| Медиа | Проверка формата и размера, подготовка фото, документов и медиагрупп | [Обработка медиа](docs/media-processing.md) |
| Диагностика | Health-check, восстановление после перезапуска и безопасные повторы | [Диагностика и восстановление](docs/health-and-recovery.md) |
| Безопасность | Доступ по Telegram ID, защита URL, секретов и локального API | [Доступ и безопасность](docs/access-and-security.md) |

Полный список документов находится в [docs/README.md](docs/README.md).

## Как устроен проект

Основной процесс обычно работает на VPS:

- принимает команды Telegram;
- хранит каналы, черновики, очередь и историю в базе данных;
- скачивает и проверяет изображения;
- публикует готовые посты;
- выдаёт домашнему news-worker задачи через небольшой API с Bearer-токеном.

Домашний news-worker нужен только для новостей. Он извлекает текст источника, передаёт его локальной модели Ollama и возвращает на VPS уже готовый черновик. Длинные статьи и расшифровки не сохраняются на VPS целиком.

## Требования

Для основного бота:

- Python 3.12 или новее;
- Telegram-бот от [@BotFather](https://t.me/BotFather);
- числовые Telegram ID администраторов;
- один или несколько каналов, где бот может публиковать сообщения;
- доступ к Telegram API и используемым источникам.

Для обработки новостей дополнительно нужны домашний компьютер, Ollama и модель `gemma4:12b` либо другая модель, одинаково указанная на VPS и домашнем worker.

## Быстрый запуск для разработки

### 1. Создайте окружение

Linux/macOS:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev,news-worker]'
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,news-worker]"
```

Если нужны PostgreSQL или SOCKS-прокси, добавьте extras `postgres` или `proxy`.

### 2. Создайте `.env`

Скопируйте `.env.example` в `.env` и заполните как минимум:

```env
BOT_TOKEN=123456:replace_with_real_token
ADMIN_IDS=123456789
DATABASE_URL=sqlite+aiosqlite:///./data/database.db
DEFAULT_CHANNEL_ALIAS=artwork
CHANNELS_JSON='{"artwork":{"chat_id":"-1001234567890","title":"Artwork","publish_mode":"auto"}}'
```

Правила:

- `ADMIN_IDS` содержит числовые ID через запятую;
- ключ `DEFAULT_CHANNEL_ALIAS` должен существовать в `CHANNELS_JSON`;
- `chat_id` канала удобнее хранить строкой;
- секреты остаются только в `.env` и не добавляются в Git;
- обычные настройки поведения находятся в `bot-settings.toml`.

Подробнее: [Конфигурация](docs/configuration.md).

### 3. Запустите бота

```bash
python -m app.main
```

Бот использует long polling, поэтому входящий публичный порт для Telegram не нужен.

## Включение обработки новостей

На VPS задайте длинный случайный секрет:

```env
NEWS_WORKER_TOKEN=replace_with_a_long_random_secret
```

По умолчанию API worker слушает `127.0.0.1:8091`. Не открывайте его напрямую в интернет. Используйте SSH-туннель, VPN или другой закрытый канал.

На домашнем компьютере:

```bash
ollama pull gemma4:12b
python -m pip install -e '.[news-worker]'
```

В домашнем `.env` укажите тот же секрет:

```env
HOME_WORKER_TOKEN=replace_with_the_same_secret
HOME_WORKER_VPS_API_URL=http://127.0.0.1:8091
HOME_WORKER_OLLAMA_BASE_URL=http://127.0.0.1:11434
HOME_WORKER_OLLAMA_MODEL=gemma4:12b
```

Запуск worker:

```bash
news-home-worker
```

Для обработки одной задачи и выхода:

```bash
news-home-worker --once
```

Подробная схема и ограничения описаны в [документе о новостях](docs/news-publications.md).

## Docker Compose

```bash
cp .env.example .env
# заполните .env
docker compose up -d --build
docker compose logs -f bot
```

Compose сохраняет SQLite и загруженные файлы в именованных volumes. News API публикуется только на `127.0.0.1:8091` хоста.

## Установка через systemd

Готовые файлы:

- `deploy/telegram-image-publisher.service` — unit для запуска;
- `deploy/update.sh` — обновление только fast-forward, резервная копия SQLite и проверка сервиса после запуска.

Пошаговая установка, обновление и откат: [Развёртывание](docs/deployment.md).

Не запускайте одновременно systemd- и Docker-экземпляры с одним `BOT_TOKEN`.

## Основные команды

| Команда | Назначение |
| --- | --- |
| `/start`, `/menu` | открыть главное меню |
| `/new` | создать публикацию с изображениями |
| `/news` | создать новость |
| `/queue [alias]` | показать очередь всех каналов или одного канала |
| `/preview [job_id\|alias]` | показать ближайший или указанный пост |
| `/publish [job_id]` | отправить задание без ожидания интервала |
| `/status <job_id>` | показать состояние задания |
| `/cancel <job_id>` | отменить задание |
| `/retry <job_id>` | повторить завершившееся ошибкой задание |
| `/recent` | показать последние задания |
| `/channels` | открыть список каналов |
| `/channel_interval <alias> <30s\|15m\|2h\|1d\|0>` | изменить интервал канала |
| `/providers` | показать источники изображений |
| `/stats` | показать статистику заданий |
| `/health [full]` | быстрая или полная диагностика |
| `/help` | показать встроенную памятку |

Кнопки главного меню дают доступ к тем же действиям.

## Тесты и покрытие

```bash
python -m pytest -q
python -m pytest --cov=app --cov-report=term-missing
python -m compileall -q app
```

Тесты не требуют реального Telegram-токена, доступа к Ollama или внешним сайтам: сетевые границы подменяются тестовыми объектами.

## Структура

```text
app/
├── bot/        # Telegram UI, команды и middleware
├── db/         # модели и подключение к базе
├── news/       # извлечение новостей и домашний worker
├── providers/  # Pixiv, DeviantArt и прямые изображения
├── queue/      # фоновые workers публикации
├── services/   # бизнес-логика
└── utils/      # парсинг, URL, теги и время

docs/           # подробная документация по функциям
tests/          # модульные и интеграционные тесты
deploy/         # systemd unit и скрипт обновления
```

## Если что-то не работает

Начните с:

```bash
python -m app.main
```

или, для systemd:

```bash
sudo systemctl status telegram-image-publisher
sudo journalctl -u telegram-image-publisher -n 100 --no-pager
```

Затем запустите `/health full`. Частые причины и порядок проверки собраны в [документе по диагностике](docs/health-and-recovery.md).
