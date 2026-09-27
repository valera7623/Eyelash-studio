# Lash-book — запись к мастеру по наращиванию ресниц

SaaS в Telegram: мастер создаёт студию, клиенты записываются по ссылке `t.me/<bot>?start=<slug>`.

Недельного графика нет. Мастер открывает окно на конкретный день (`25.09 12:00 18:00`). Внутри окна запись закрепляется сразу. Вне окна — заявка, которую мастер подтверждает или отклоняет.

Оплата визита — у мастера, не в боте. ЮKassa нужна только для подписки мастера (тариф Старт).

## Кому какой вход

| Роль | Как попадает | Что видит |
|---|---|---|
| Клиент | `t.me/<bot>?start=<slug>` | Дата → время в окне или своё время заявкой |
| Владелец зала | `/studio` | Кабинет: шпаргалка, заявки, ссылка, окна, тариф |
| Админ платформы | `ADMINS` | `/admin` |
| Superadmin | `SUPERADMINS` | `/superadmin` |

## Клиент

Дата → время. Если окно открыто и время свободно — запись сразу. Если окна нет — заявка. Оплата — у мастера. `/my` — отмена. Напоминания за 24 ч и за 2 ч.

## Мастер

`/studio` → название студии. Ссылка `t.me/<bot>?start=<slug>`.

Free: 1 зал, 10 записей/мес. Старт 490 ₽ — без лимита. Везде один зал, кабинет для мастера-одиночки. Сколько займёт визит, заранее не спрашиваем: у каждого мастера по-своему.

## Запуск

```bash
cp .env.example .env
# BOT_TOKEN
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python create_tables.py
python -m src.main
```

Если `api.telegram.org` не открывается, задайте `TELEGRAM_PROXY`.

Тесты: `pytest`

Публичный лендинг: `/` — подписка мастера, оферта, кнопка в бота. HTTP `:8089` на VPS. Домен: `https://eyelash.com.ru`.

Docker: `docker compose up -d --build`. База: `data/eyelash.db`.

## Деплой

Прод: VPS `185.106.95.16`, каталог `/home/valera/eyelash-studio`, HTTP `:8089`, Redis `:6380`.

Автодеплой: push в `main` репозитория [valera7623/Eyelash-studio](https://github.com/valera7623/Eyelash-studio) → GitHub Actions собирает образ `ghcr.io/valera7623/eyelash-studio`, копирует `deploy/traefik.yml` в `/home/valera/traefik/dynamic/eyelash.yml` и перезапускает контейнер. Вручную: Actions → Deploy eyelash-studio to VPS → Run workflow.

Секреты репозитория: `VPS_HOST`, `VPS_USERNAME`, `VPS_SSH_KEY`, `BOT_TOKEN`.

Первый `.env` на VPS должен содержать `PUBLIC_BASE_URL=https://eyelash.com.ru` (Actions дописывает, если строки нет).

Локально на VPS без Actions: `./scripts/deploy-vps.sh`.

Разовая установка Traefik, если файл ещё не на сервере:

```bash
sudo mkdir -p /home/valera/traefik/dynamic
sudo cp deploy/traefik.yml /home/valera/traefik/dynamic/eyelash.yml
```
