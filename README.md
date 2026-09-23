# Бот записи в зал для наращивания ресниц

SaaS в Telegram, как tele-bot-2: мастер создаёт свою студию и зал, клиенты записываются по ссылке.

Разница с фотозалом: **недельного графика нет**. Мастер открывает окно на конкретный день (`25.09 12:00 18:00`). Внутри окна клиент занимает слот сразу. Вне окна — заявка, которую мастер подтверждает или отклоняет.

## Кому какой вход

| Роль | Как попадает | Что видит |
|---|---|---|
| Клиент | `t.me/<bot>?start=<slug>` | Зал → дата → длительность → слот в окне или своё время заявкой |
| Владелец зала | `/studio` | Кабинет: окна, заявки, ссылка и QR, тариф |
| Админ платформы | `ADMINS` | `/admin` |
| Superadmin | `SUPERADMINS` | `/superadmin` |

## Клиент

Услуга почасовой аренды зала. Если окно открыто и время свободно — hold и предоплата как в tele-bot-2. Если окна нет — заявка. `/my` — отмена. Напоминания за 24 ч и за 2 ч.

## Мастер

`/studio` → название студии → название зала → цена часа. Ссылка `t.me/<bot>?start=<slug>`.

Free: 1 зал, 10 записей/мес. Старт 490 ₽ — без лимита. Везде один зал, кабинет для мастера-одиночки.

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

Docker: `docker compose up -d --build`. База: `data/eyelash.db`.

## Деплой

Прод: VPS `185.106.95.16`, каталог `/home/valera/eyelash-studio`, HTTP `:8089`, Redis `:6380` (studio-book занимает `:8088` / `:6379`).

Автодеплой: push в `main` репозитория [valera7623/Eyelash-studio](https://github.com/valera7623/Eyelash-studio) → GitHub Actions собирает образ `ghcr.io/valera7623/eyelash-studio` и перезапускает контейнер. Вручную: Actions → Deploy eyelash-studio to VPS → Run workflow.

Секреты репозитория: `VPS_HOST`, `VPS_USERNAME`, `VPS_SSH_KEY`, `BOT_TOKEN`.

Локально на VPS без Actions: `./scripts/deploy-vps.sh`.
