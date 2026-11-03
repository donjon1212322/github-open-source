# Секреты и переменные: что нужно и где взять

**Репозиторий:** `donjon1212322/github-open-source` · **Режим:** production

> ⚠️ Значения Secrets добавляйте только в GitHub → **Settings → Secrets and
> variables → Actions → Secrets**. Не коммитьте их, не держите в `.env` и не
> присылайте в общий чат. Variables не секретны, но тоже задаются в GitHub UI.
> GitHub **не отдаёт значения Secrets через API** — только имена, поэтому при
> переносе в новый репозиторий их нужно записать заново.

---

## 1. Семь Secrets

| Secret | Где взять | Примечание |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram → **@BotFather** → `/newbot` → скопировать токен | Бота сделать **администратором** канала; `/setabouttext` и `/setuserpic` опциональны |
| `GEMINI_API_KEY` | <https://aistudio.google.com/> → **Get API key** | Бесплатный тариф; модель задаётся Variable `GEMINI_MODEL` |
| `IMGBB_API_KEY` | <https://imgbb.com/> → аккаунт → **API key** | Хостинг карточек 1200×630 |
| `TELEGRAPH_ACCESS_TOKEN` | Одноразово: `https://api.telegra.ph/createAccount?short_name=GitHubOpenSource&author_name=GitHub%20Open%20Source` | Сохранить сразу: повторный вызов создаст новый аккаунт |
| `DEV_API_KEY` | dev.to → **Settings → Extensions → DEV API Keys** → Generate API Key | Тот же аккаунт, что публикует статьи |
| `BLUESKY_APP_PASSWORD` | bsky.app → **Settings → Privacy & Security → App Passwords** → Add App Password | Не основной пароль; handle — в `BLUESKY_HANDLE` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Google Cloud Console → см. раздел 3 | Один строкой, весь JSON; для записи в Google Sheet |

**Не нужен и не запрашивается:** `FACEBOOK_PAGE_ACCESS_TOKEN`,
`FACEBOOK_PAGE_ID`, `FEATURE_FACEBOOK`, `FEATURE_FACEBOOK`-легаси, любые ключи
удалённой соцсети, а также отдельный GitHub PAT (workflow используют
встроенный `${{ github.token }}`).

---

## 2. Шестнадцать Variables

| Variable | Значение | Комментарий |
|---|---|---|
| `APP_ENV` | `production` | Меняется только вместе со следующими двумя |
| `PUBLISH_TARGET` | `main` | Target lock: `main` недоступен без трёх переменных сразу |
| `TELEGRAM_MAIN_CHANNEL_ID` | `@…` реального канала | Все исходящие CTA ведут сюда |
| `MAIN_TELEGRAM_CHANNEL` | то же значение | Legacy-алиас, читается `config.py` |
| `TELEGRAM_TEST_CHANNEL_ID` | пусто в проде | Приватный 1:1 канал только для приёмки |
| `TELEGRAM_OWNER_CHAT_ID` | числовой chat id | Отчёты и алерты владельцу; взять у @userinfobot |
| `TELEGRAM_SOURCE_CHANNELS` | публичные каналы через запятую | Источники discovery, без `@` |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | До миграции модели |
| `BLUESKY_HANDLE` | `name.bsky.social` | Тот же аккаунт, что app password |
| `FACEBOOK_SHEETS_ID` | ID между `/d/` и `/edit` | Из URL таблицы |
| `FACEBOOK_SHEETS_RANGE` | `facebook!A1` | Имя вкладки + `!A1`; дефолт уже в коде |
| `FEATURE_TELEGRAPH` | `true` | Полный разбор на Telegraph |
| `FEATURE_DEVTO` | `true` | Статья дня |
| `FEATURE_BLUESKY` | `true` | Кросс-пост лучшего поста дня |
| `FEATURE_AI_BRIEF` | `true` | Ежедневный AI Brief |
| `FEATURE_SHEETS_HANDOFF` | `true` | Только после шагов 3–4 |

---

## 3. `GOOGLE_SERVICE_ACCOUNT_JSON` по шагам

1. <https://console.cloud.google.com/> → создать/выбрать проект
   (например `ghos-sheets`).
2. **«API и службы» → «Включить API и сервисы»** → **Google Sheets API** →
   **Включить**. Без этого запись упадёт с 403/404.
3. **«IAM и администрирование» → «Сервисные аккаунты» →
   «Создать сервисный аккаунт»**. Роль на проект давать не обязательно.
4. Открыть аккаунт → **«Ключи» → «Добавить ключ» → «Создать новый ключ» →
   JSON** → скачать. Скопировать `client_email` вида
   `name@….iam.gserviceaccount.com`.
5. Превратить JSON в одну строку:
   `python -c "import json;print(json.dumps(json.load(open('key.json'))))"`
   (или `jq -c . key.json`) → вставить в Secret
   `GOOGLE_SERVICE_ACCOUNT_JSON`.
6. Удалить скачанный `key.json` с компьютера. При утечке — удалить ключ в
   консоли и создать новый.

---

## 4. Таблица, доступ и make.com

1. Создать Google-таблицу с колонками A–E:
   `project_id | content | platform | media_urls | title`.
2. **Share** → вставить `client_email` из п. 3 → роль **«Редактор»**.
   Без этого будет `403 Permission denied`.
3. В make.com: **Google Sheets → Watch New Rows** (таблица и диапазон выше) →
   **Facebook Pages → Create a Post** (`message` ← B, картинка ← D,
   заголовок ← E). Сценарий **ON**, соединение Facebook авторизовано на Page.
4. Только после этого выставлять `FEATURE_SHEETS_HANDOFF=true`. До этого
   `facebook-handoff` — безопасный no-op.

Проверка: `ghos-v6 facebook-handoff --limit 1` → в логах `queued: 1`,
в state событие `sheet_row_queued`, в таблице новая строка. Ошибка
`sheets_not_configured` = нет Secret или ID таблицы.

---

## 5. Порядок запуска

1. Записать 7 Secrets и 16 Variables.
2. `ghos-v6 doctor` → 13/13.
3. Включить `Doctor` и `Publishing pipeline`.
4. Один `publish` + визуальная проверка поста и статьи.
5. Проверить CI/CodeQL, 13 cron-маршрутов и то, что `commit-state` пишет state.

---

## 6. После запуска

- Отозвать временный setup PAT: **Settings → Developer settings →
  Personal access tokens → Fine-grained tokens → Revoke**.
- Ротировать ключи, которые побывали в переписке.
- Убедиться, что прежний бот остановлен и не пишет в тот же канал.

Связанные документы: [`FACEBOOK_PIPELINE.md`](FACEBOOK_PIPELINE.md),
[`RUNBOOK.md`](RUNBOOK.md), [`SECURITY.md`](../SECURITY.md),
[`HANDOFF.md`](HANDOFF.md).
