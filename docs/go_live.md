# Касса в проде и пилот за вечер

Ключи и живые платежи — только после одобрения кассы. Секреты в git не класть.

Предпочтительно **ЮKassa** (тот же кабинет, что gameforge/agentops, но **отдельный магазин** под Studio-book).

Webhook ЮKassa: `POST https://studiobook.com.ru/yookassa/webhook`

Webhook Prodamus (запас): `POST https://studiobook.com.ru/prodamus/webhook`

## 0. Ключи на VPS

Не копируйте `shopId` / секрет с agentops.com.ru или gameforge.website: у каждого магазина свой webhook.

В кабинете ЮKassa: название текущего магазина → **Добавить магазин** → «На сайте» → `https://studiobook.com.ru`. Затем Интеграция → HTTP-уведомления: `https://studiobook.com.ru/yookassa/webhook` (событие `payment.succeeded`). Интеграция → Ключи API → секретный ключ.

В `/home/valera/studio-book/.env`:

```
YOOKASSA_SHOP_ID=...
YOOKASSA_SECRET_KEY=...
PAYMENT_PROVIDER=auto
PUBLIC_BASE_URL=https://studiobook.com.ru
```

`auto` включает ЮKassa, если ключи заданы, иначе Prodamus.

Запас Prodamus:

```
PRODAMUS_PAYFORM_URL=https://payform.ru/....
PRODAMUS_SECRET=...
PRODAMUS_SHOP_ID=...
```

Пересоздать контейнер, чтобы подтянуть env:

```
cd /home/valera/studio-book && docker compose -f docker-compose.prod.yml up -d --force-recreate --no-build
```

Проверка: в боте `/admin` строка «Касса: ЮKassa». Пока ключей нет — «Касса: нет».

## 1. Три платежа на живом боте

1. **Слот.** Своя студия в `/studio` → ссылка клиенту → слот с ценой > 0 → оплата картой/СБП → бронь `paid`, чек от кассы.
2. **Подписка.** `/studio` → «Тариф» → Старт → оплата → тариф в кабинете.
3. **Возврат.** Клиент `/my` → отмена в окне бесплатной отмены → статус возврата в `/admin` (`refunded`) и в кабинете ЮKassa (или Prodamus).

Если клиент видит «предоплата ещё не подключена» — ключей в процессе нет или контейнер не перечитали.

## 2. Пилот «шаблон за вечер»

До рассылки по каталогам — одна реальная студия (своя или дружеская).

- Владелец за вечер: название → зал → часы/цены/правила → «Ссылка» и «Тексты».
- Клиент по ссылке оплачивает слот; студия видит бронь; приходит напоминание за 24 ч или 2 ч.
- Отмена по правилу студии возвращает деньги через кассу.

Если на каждую студию нужен саппорт — чинить тексты в `/studio`, не модули.

Живой бот: **`@Studio_book_bot`**. Username берётся из токена (`getMe`); в `.env` на VPS:

```
BOT_TOKEN=...          # токен @Studio_book_bot из BotFather
BOT_USERNAME=Studio_book_bot
```

После смены токена пересоздать контейнер. В `/studio` заново открыть «Ссылка» — QR и `t.me/Studio_book_bot?start=<slug>` обновятся. Старый `@Saas_concept_bot` не поллится: старые диплинки на него не работают.

## 3. Бэкап и восстановление SQLite

Планировщик в 03:15 MSK пишет `data/backups/studio_book-YYYYMMDD-HHMM.db` (online backup, не `cp` живого файла). На VPS:

```
ls -lt /home/valera/studio-book/data/backups/ | head
```

Разовая копия без ожидания крона:

```
docker compose -f docker-compose.prod.yml exec bot python scripts/backup_sqlite.py
```

Restore (бот не пишет в файл во время копирования):

```
cd /home/valera/studio-book
docker compose -f docker-compose.prod.yml stop bot
cp data/studio_book.db data/studio_book.before-restore.db
cp data/backups/studio_book-YYYYMMDD-HHMM.db data/studio_book.db
docker compose -f docker-compose.prod.yml start bot
```

Проверка: `/admin` открывается, список броней в `/studio` на месте.

Off-site: в `.env` задайте каталог на **другом диске** (не тот же `data/`):

```
BACKUP_OFFSITE_DIR=/offsite
```

В `docker-compose.prod.yml` раскомментируйте том `/mnt/studio-book-offsite:/offsite`. Планировщик после локальной копии делает `copy2` туда (те же 14 файлов). Если каталог недоступен — локальный бэкап всё равно пишется, ошибка в логе.

Разовая проверка:

```
docker compose -f docker-compose.prod.yml exec bot python scripts/backup_sqlite.py
ls -lt /mnt/studio-book-offsite | head
```

Не Cloud Supabase и не зарубежный S3: ПДн только в РФ.

## 4. iCal после смены BOT_TOKEN

Подпись ленты — `ICAL_FEED_SECRET` (или файл `data/.ical_secret`), не токен бота. После ротации BotFather старые URL подписки продолжают открываться (принимается и HMAC от прежнего `BOT_TOKEN`). Новый URL в `/studio` → «iCal». Чтобы отозвать старые ссылки — смените `ICAL_FEED_SECRET` и заново отправьте ленту владельцу.

## 5. Дальше не код

8–10 разговоров: [pain_check.md](pain_check.md). Воронку вести вне репозитория.

Аутрич по каталогам: [outreach.md](outreach.md). Kill 3–5% за 3 месяца — менять оффер, не кабинет.
