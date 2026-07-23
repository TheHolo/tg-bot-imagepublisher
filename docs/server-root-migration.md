# Переезд существующей systemd-установки в корень приложения

Эта инструкция переносит уже работающую установку из `/opt/telegram-image-publisher/tg-bot-imagepublisher` непосредственно в `/opt/telegram-image-publisher`.

Используется безопасная схема copy-and-swap:

1. действующая установка останавливается;
2. её содержимое копируется в новый соседний каталог без старого `.venv`;
3. новый код переводится на `origin/main`, зависимости устанавливаются заново;
4. исходный каталог переименовывается в rollback-копию;
5. подготовленный каталог атомарно занимает окончательный путь.

Исходная установка не удаляется. До завершения проверки её можно вернуть обратным переименованием.

## Перед началом

- Убедитесь, что актуальная версия уже опубликована в `origin/main`.
- Прочитайте инструкцию целиком до выполнения команд.
- Выполняйте команды в одном root-shell или в одной SSH-сессии: дальнейшие шаги используют объявленные переменные.
- Запланируйте окно обслуживания. С момента остановки service и до запуска из нового каталога бот отвечать не будет.
- Убедитесь, что в `/opt` достаточно места для второй копии `data` и `storage`.
- Если используется PostgreSQL, сделайте отдельный дамп штатным инструментом PostgreSQL. Команды ниже сохраняют файлы установки, но не заменяют серверный backup базы.

Инструкция рассчитана на service `telegram-image-publisher` и пользователя `telegram-publisher`. Если в вашей установке они называются иначе, измените значения переменных.

## 1. Откройте root-shell и задайте пути

```bash
sudo -i

MIGRATION_SERVICE='telegram-image-publisher'
MIGRATION_USER='telegram-publisher'
MIGRATION_ROOT='/opt/telegram-image-publisher'
MIGRATION_LEGACY="${MIGRATION_ROOT}/tg-bot-imagepublisher"
MIGRATION_STAMP="$(date -u +%Y%m%d-%H%M%S)"
MIGRATION_NEW="/opt/telegram-image-publisher.new-${MIGRATION_STAMP}"
MIGRATION_BACKUP="/opt/telegram-image-publisher.before-${MIGRATION_STAMP}"
MIGRATION_UNIT_BACKUP="/opt/telegram-image-publisher.service.before-${MIGRATION_STAMP}"
```

Выведите значения и убедитесь, что они указывают на ожидаемые каталоги:

```bash
printf 'legacy: %s\nnew: %s\nbackup: %s\n' \
  "$MIGRATION_LEGACY" "$MIGRATION_NEW" "$MIGRATION_BACKUP"
```

## 2. Выполните предварительные проверки

Проверьте пользователя, service, Git-репозиторий, `.env` и виртуальное окружение:

```bash
id "$MIGRATION_USER"
systemctl cat "$MIGRATION_SERVICE"
test -d "$MIGRATION_LEGACY/.git"
test -f "$MIGRATION_LEGACY/.env"
test -x "$MIGRATION_LEGACY/.venv/bin/python"
```

Каждая команда должна завершиться без ошибки. Затем проверьте ветку и tracked-изменения:

```bash
sudo -H -u "$MIGRATION_USER" \
  git -C "$MIGRATION_LEGACY" status --short --branch

sudo -H -u "$MIGRATION_USER" \
  git -C "$MIGRATION_LEGACY" status --porcelain --untracked-files=no
```

Вторая команда не должна вывести ничего. Если она показывает изменённые tracked-файлы, остановитесь и отдельно сохраните или закоммитьте их: инструкция не должна молча перезаписывать серверные изменения.

Проверьте объём установки и свободное место:

```bash
du -sh "$MIGRATION_LEGACY"
df -h /opt
```

Установите инструменты, необходимые для копирования и нового virtualenv. Для Ubuntu 24.04:

```bash
apt update
apt install -y rsync python3.12 python3.12-venv
python3.12 --version
```

## 3. Остановите бота и сохраните unit-файл

```bash
systemctl stop "$MIGRATION_SERVICE"
systemctl is-active "$MIGRATION_SERVICE"
```

Ожидаемый ответ второй команды — `inactive`. Не продолжайте, пока service остаётся активным.

Сохраните установленный unit для отката:

```bash
test -f "/etc/systemd/system/${MIGRATION_SERVICE}.service"
cp -a "/etc/systemd/system/${MIGRATION_SERVICE}.service" \
  "$MIGRATION_UNIT_BACKUP"
chmod 0600 "$MIGRATION_UNIT_BACKUP"
```

## 4. Создайте новую копию установки

Новый каталог должен отсутствовать. Команда `install` завершится ошибкой, если переменная неожиданно пуста, а последующая проверка не позволит использовать существующий каталог:

```bash
test ! -e "$MIGRATION_NEW"
install -d -o "$MIGRATION_USER" -g "$MIGRATION_USER" -m 0750 \
  "$MIGRATION_NEW"
```

Скопируйте Git-репозиторий, `.env`, SQLite, storage и остальные файлы. Старый virtualenv намеренно исключён: его исполняемые скрипты содержат абсолютный прежний путь.

```bash
rsync -a --exclude='.venv/' \
  "$MIGRATION_LEGACY/" \
  "$MIGRATION_NEW/"

chown -R "$MIGRATION_USER:$MIGRATION_USER" "$MIGRATION_NEW"
chmod 0600 "$MIGRATION_NEW/.env"
```

Проверьте ключевые файлы новой копии:

```bash
test -d "$MIGRATION_NEW/.git"
test -f "$MIGRATION_NEW/.env"
test -f "$MIGRATION_NEW/pyproject.toml"
test -f "$MIGRATION_NEW/deploy/telegram-image-publisher.service"
```

## 5. Переведите новую копию на `origin/main`

```bash
sudo -H -u "$MIGRATION_USER" \
  git -C "$MIGRATION_NEW" fetch origin main

if sudo -H -u "$MIGRATION_USER" \
  git -C "$MIGRATION_NEW" show-ref --verify --quiet refs/heads/main; then
  sudo -H -u "$MIGRATION_USER" git -C "$MIGRATION_NEW" switch main
else
  sudo -H -u "$MIGRATION_USER" \
    git -C "$MIGRATION_NEW" switch --track -c main origin/main
fi

sudo -H -u "$MIGRATION_USER" \
  git -C "$MIGRATION_NEW" merge --ff-only origin/main
```

Убедитесь, что выбрана ветка `main`, а локальный commit совпадает с `origin/main`:

```bash
sudo -H -u "$MIGRATION_USER" git -C "$MIGRATION_NEW" branch --show-current
sudo -H -u "$MIGRATION_USER" git -C "$MIGRATION_NEW" rev-parse HEAD
sudo -H -u "$MIGRATION_USER" git -C "$MIGRATION_NEW" rev-parse origin/main
```

Последние две команды должны вывести одинаковый hash.

## 6. Создайте новый virtualenv и проверьте приложение

Extras `proxy` и `postgres` устанавливаются вместе. Это безопасно и не требует заранее определять, какой backend или proxy использует текущий `.env`.

```bash
sudo -H -u "$MIGRATION_USER" \
  python3.12 -m venv "$MIGRATION_NEW/.venv"

sudo -H -u "$MIGRATION_USER" \
  "$MIGRATION_NEW/.venv/bin/python" -m pip install --upgrade pip

sudo -H -u "$MIGRATION_USER" \
  "$MIGRATION_NEW/.venv/bin/python" -m pip install -e \
  "${MIGRATION_NEW}[proxy,postgres]"
```

Загрузите настройки без вывода секретов и проверьте Python-файлы:

```bash
cd "$MIGRATION_NEW"

sudo -H -u "$MIGRATION_USER" .venv/bin/python -c \
  'from app.config import Settings; s = Settings(); assert s.admin_ids; assert s.default_channel_alias in s.channels_json; print("Configuration OK")'

sudo -H -u "$MIGRATION_USER" \
  .venv/bin/python -m compileall -q app
```

Если проверка завершилась ошибкой, не выполняйте swap. Исправьте новую копию или удалите только созданный `MIGRATION_NEW`, оставив работающую структуру нетронутой.

## 7. Выполните swap каталогов

На этом шаге исходный корневой каталог переименовывается в rollback-копию. Ничего не удаляется.

```bash
cd /opt
test ! -e "$MIGRATION_BACKUP"

mv -- "$MIGRATION_ROOT" "$MIGRATION_BACKUP"
mv -- "$MIGRATION_NEW" "$MIGRATION_ROOT"
```

Проверьте новую структуру:

```bash
test -d "$MIGRATION_ROOT/.git"
test -x "$MIGRATION_ROOT/.venv/bin/python"
test -f "$MIGRATION_ROOT/.env"
test -d "$MIGRATION_BACKUP/tg-bot-imagepublisher/.git"
```

Последняя команда подтверждает, что исходная установка сохранена внутри rollback-копии.

## 8. Установите актуальный unit и запустите service

```bash
install -m 0644 \
  "$MIGRATION_ROOT/deploy/telegram-image-publisher.service" \
  "/etc/systemd/system/${MIGRATION_SERVICE}.service"

systemctl daemon-reload
systemctl start "$MIGRATION_SERVICE"
sleep 8
systemctl is-active "$MIGRATION_SERVICE"
systemctl status "$MIGRATION_SERVICE" --no-pager --lines=20
journalctl -u "$MIGRATION_SERVICE" -n 100 --no-pager
```

Ожидаемый статус — `active`. Проверьте, что systemd использует окончательный корень:

```bash
systemctl show "$MIGRATION_SERVICE" \
  --property=User --property=WorkingDirectory --property=ExecStart
```

Затем отправьте боту:

```text
/health
/channels
```

После этого выполните тестовую публикацию. Проверьте, что существующая очередь, история и настройки каналов сохранились.

## 9. Проверьте штатное обновление

```bash
cd /opt/telegram-image-publisher
./deploy/update.sh
```

Скрипт должен принять каталог и ветку `main`, создать backup SQLite и завершить health-check service. Поскольку текущая сессия уже root, дополнительный `sudo` не требуется.

## Откат

Если после swap бот не запускается или данные выглядят неверно, верните исходную установку. Переменные из первого шага должны оставаться в текущем shell.

```bash
systemctl stop "$MIGRATION_SERVICE"

MIGRATION_FAILED="/opt/telegram-image-publisher.failed-${MIGRATION_STAMP}"
test ! -e "$MIGRATION_FAILED"

mv -- "$MIGRATION_ROOT" "$MIGRATION_FAILED"
mv -- "$MIGRATION_BACKUP" "$MIGRATION_ROOT"

cp -a "$MIGRATION_UNIT_BACKUP" \
  "/etc/systemd/system/${MIGRATION_SERVICE}.service"

systemctl daemon-reload
systemctl start "$MIGRATION_SERVICE"
systemctl status "$MIGRATION_SERVICE" --no-pager --lines=20
```

После отката новая неудачная копия остаётся в `MIGRATION_FAILED` для диагностики. Не удаляйте backup-каталоги, пока не убедитесь, что новая установка стабильно работает и резервная копия базы доступна.

## После успешного переезда

- Оставьте `MIGRATION_BACKUP` как минимум до следующего успешного обновления и контрольной публикации.
- Сохраните `MIGRATION_UNIT_BACKUP` вместе с остальными резервными копиями.
- Убедитесь, что мониторинг и внешние backup-задачи используют `/opt/telegram-image-publisher`.
- Все дальнейшие обновления выполняйте из `/opt/telegram-image-publisher` командой `sudo ./deploy/update.sh`.
