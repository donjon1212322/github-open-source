# Facebook: как это устроено и что мы меняем

## Что есть сейчас (ваша рабочая схема, не трогаем)

```
github-projects-bot
   └─ formated_facebook.py  →  data/facebook_posts.json
        └─ google_sheets.py (сервисный аккаунт Google)
             └─ Google Sheet: project_id | content | platform | media_urls
                  └─ make.com → Facebook Page
```

Схема рабочая и бесплатная. Единственное, что в ней плохо — **текст**
(см. `docs/agents/FACEBOOK_REVIEW.md`: faux-bold, дубли, 6 разделителей,
7 фич, «0 open issues», картинка — авто-карточка GitHub).

## Что делает v6

v6 не заменяет make.com. Он становится **поставщиком строк** в ту же
таблицу — тем же сервисным аккаунтом, в те же колонки:

```
ghos-v6 publish        → пост в Telegram + карточка 1200×630
ghos-v6 facebook-handoff → строка в Sheet  → make.com → Facebook Page
```

Почему так, а не напрямую через Graph API:

| | Hand-off в Sheet + make.com | Прямой Graph API из v6 |
|---|---|---|
| Токен | не нужен (сервисный аккаунт уже есть) | нужен Page-токен, которого нет |
| Ретраи | делает make.com | писать самим |
| Вы уже умеете | да | нет |
| Картинка | URL из колонки `media_urls` | нужен публичный хост |

Поэтому **единственный** путь — hand-off. Прямой `FacebookClient` удалён из
рабочего контура the current release (`config.py`, `pipeline.py`, `doctor.py`,
workflow): он остался в `platforms.py` только как справочная реализация.
Флага `FEATURE_FACEBOOK` больше нет, `FACEBOOK_PAGE_ID` и
`FACEBOOK_PAGE_ACCESS_TOKEN` не читаются и не передаются.

## Колонки

| Колонка | Что | Откуда |
|---|---|---|
| `project_id` | `repo_id`, иначе `full_name` | запись публикации |
| `content` | готовый текст поста | `content_adapters.build_Facebook_text` |
| `platform` | `facebook` | константа |
| `media_urls` | URL карточки 1200×630 | `image_url` записи |
| `title` | короткое имя проекта (новая, для сценариев, которым нужен заголовок) | `name` |

Первые четыре колонки **совпадают** с тем, что писала старая схема, —
существующий сценарий make.com продолжит работать без правок.

## Включение

1. Убедитесь, что сервисный аккаунт имеет доступ на редактирование к
   таблице (тот же, что сейчас пишет из `google_sheets.py`).
2. GitHub → Settings → Secrets and variables → Actions:
   - **Secret** `GOOGLE_SERVICE_ACCOUNT_JSON` — JSON сервисного аккаунта целиком.
   - **Variable** `FACEBOOK_SHEETS_ID` — id таблицы.
   - **Variable** `FACEBOOK_SHEETS_RANGE` — лист и ячейка, по умолчанию `facebook!A1`.
   - **Variable** `FEATURE_SHEETS_HANDOFF=true` (по умолчанию выключено).

`FACEBOOK_PAGE_ACCESS_TOKEN` и `FACEBOOK_PAGE_ID` **не нужны и не
читаются**: в Facebook публикует make.com, а v6 только пишет строку в
таблицу. Единственный флаг — `FEATURE_SHEETS_HANDOFF`. Прямой Graph API из
v6 удалён, поэтому лишних «непонятных» Facebook-переменных в настройках
больше нет.

**Что должен дать владелец:** (1) JSON сервисного аккаунта одной строкой в
Secret `GOOGLE_SERVICE_ACCOUNT_JSON`; (2) id таблицы в `FACEBOOK_SHEETS_ID`;
(3) расшарить таблицу на e-mail этого сервисного аккаунта с ролью
«Редактор»; (4) включить сценарий make.com, который читает лист.
Диапазон `FACEBOOK_SHEETS_RANGE` уже имеет рабочий дефолт `facebook!A1`.
3. Проверка без публикации:

```bash
ghos-v6 facebook-handoff --limit 1
```

Команда вернёт `{"queued": 1, "range": "facebook!A5:F5", ...}` и запишет
строку. make.com подхватит её как обычно.

## Гарантии

- **Падение Sheets не ломает Telegram.** Ошибка логируется в
  `events.jsonl` как `facebook_handoff_failed`, публикация в канал
  продолжается.
- **Никаких двойных публикаций.** Запись получает
  `crossposts.facebook_sheet.status = "queued"`, повторный запуск её не
  выберет.
- **Выключено по умолчанию.** Без `FEATURE_SHEETS_HANDOFF=true` команда
  отвечает `feature_disabled` и ничего не пишет.

## Что нужно решить вам

Сейчас `media_urls` берётся из `image_url` записи. Для постов, где картинка
хостилась локально (ImgBB), это публичный URL — годится. Но у самых первых
записей там `opengraph.githubassets.com`, то есть та самая авто-карточка
GitHub, которую мы хотим заменить. После включения ImgBB-хостинга в
пайплайне (он уже есть, `media_host.py`) в таблицу пойдёт наша карточка.
