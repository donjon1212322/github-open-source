# RUNBOOK — настройка, запуск, откат

**Репозиторий:** `donjon1212322/github-open-source` · **Ветка:** `main`

---

## 1. Предварительные условия

- Публичный репозиторий с одним коммитом и пустым state.
- 7 Secrets и 16 Variables записаны — [`SECRETS_CHECKLIST.md`](SECRETS_CHECKLIST.md).
- actions включены; `Workflow permissions` не запрещает job-level
  `permissions: contents: write` (это нужно `commit-state`).
- Прежний бот остановлен: все его workflow отключены, активных запусков нет.

---

## 2. Порядок запуска

1. **Doctor.** `command=doctor` в `Doctor` workflow. Ожидается 13/13,
   `telegram_get_me` с нужным ботом, `telegram_test_channel` (если приёмка в
   тестовом канале) и отсутствие ошибок у внешних клиентов.
2. **CI и CodeQL.** Оба зелёные на чистом коммите. Локально:
   `pytest` (305 тестов, порог покрытия 65 %), `ruff check .`, `mypy src`,
   `python tools/secret_scan.py`, `python tools/mutation_check.py` (30/30).
3. **Один пост.** `command=publish`, `slot=manual`. Проверить карточку
   1200×630, разделитель между подписью канала и тегами, отсутствие
   приватных `t.me/c/` ссылок и то, что все CTA ведут на реальный канал.
4. **Статья.** `command=article` или `command=article-update` (последняя
   делает `PUT` по существующему ID, а не создаёт дубликат). Проверить H2,
   списки и горизонтальные разделители на отрендеренной странице.
5. **Включить расписание.** 13 cron-маршрутов: 3 слота публикаций, кросс-посты,
   статья дня, AI Brief, дайджест/опрос, `poll-close`/`learn`, метрики,
   недельный отчёт, moderation, facebook-handoff.
6. **Проверить state.** `commit-state` коммитит `state/state.json` и
   `state/events.jsonl`; `concurrency: state-writer` не даёт двум задачам
   писать одновременно. Диспатчить команды последовательно, не батчем.

---

## 3. Target lock

`APP_ENV=production`, `PUBLISH_TARGET=main`, `TELEGRAM_MAIN_CHANNEL_ID` —
**три переменные меняются только одновременно**. Пустой тестовый ID никогда не
подменяется основным каналом. Любая приёмка в тестовом канале идёт при
`APP_ENV=test` / `PUBLISH_TARGET=test`, но все исходящие ссылки по-прежнему
ведут на реальный канал.

---

## 4. Ежедневная работа и инциденты

См. [`OPERATIONS.md`](OPERATIONS.md). Кратко:

- Упавший кросс-пост не откатывает Telegram-пост; он уходит в `dead_letters`.
- Публикация без вердикта Gemini пропускает слот (fail-closed).
- `state.json` с неверной формой поднимает `StateCorruptError`, а не
   обнуляется молча.
- При подозрении на утечку — ротация ключа в сервисе и запись нового значения
  в GitHub Secrets; старое значение удалить из всех копий.

### 4.1 Обложка dev.to (1000×420)

dev.to не показывает `main_image` как есть: он прогоняет его через
`width=1000,height=420,fit=cover,gravity=auto`. Карточка 1200×630 при этом
теряет сверху и снизу ~63 px — обрезаются шапка «The Open Source Daily» и
строка со звёздами/форками. Социальная карточка 1200×627, наоборот, режет
края по бокам.

Поэтому `DevToClient` берёт `record.devto_cover_url`, а не `image_url`.
`pipeline._ensure_devto_cover()` скачивает уже загруженную карточку,
`devto_cover.build_devto_cover()` вписывает её целиком в холст 1000×420
(масштаб 760×399 по центру, поля цвета бумаги) и загружает на ImgBB.
Telegram, Bluesky и Telegraph продолжают получать исходную 1200×630.

Чтобы перерисовать обложку у **уже опубликованной** статьи, запустите
`Publishing pipeline → article-update`: команда делает `PUT
/api/articles/{id}` по id из `state/state.json`, без создания дубликата.
Проверка: `GET /api/articles/{id}` → поле `cover_image` должно содержать
URL с `-devto-cover.png`.

### 4.2 Отдельная ветка `state` (фаза B/C, 03.11.2026)

Живое состояние **не лежит в `main`**. В `main` только пустой bootstrap
(`state/state.json` = `DEFAULT_STATE`, `state/events.jsonl` пуст), а
`state/state.json` и `state/events.jsonl` с реальными данными находятся в
orphan-ветке `state` того же репозитория.

| Кто | Что делает |
|---|---|
| `run` | `fetch-depth: 1`, затем `python tools/state_branch.py read` — скачивает два файла с ветки `state` через API. Никакого полного клона и `git reset --hard`. |
| `Upload state` | артефакт создаётся **только если хэш состояния изменился** (`state_branch.py digest`), а не на каждом из 64 запусков в сутки. |
| `commit-state` | `contents: write`, один атомарный CAS-коммит на ветку `state` через Git Data API (`PATCH git/refs/heads/state`, `force: false`). При гонке — повтор до 5 раз. В `main` не пишет ничего. |
| `Lifecycle guard` | раз в неделю `status` (размер, свежесть, «не протёк ли state обратно в main»); первый понедельник месяца — `squash` ветки в один parentless-коммит. Общая группа `state-writer` не даёт squash пересечься с записью. |
| CI | `check-bootstrap`: если в `main` снова появились записи — сборка красная. |
| Doctor | `status` как живой heartbeat. |

Инварианты, которые нельзя нарушать:

- `state_branch.py` — stdlib только, без секретов публикации; авторизация
  исключительно `github.token`.
- `write` отказывается коммитить state, где число `published` уменьшилось,
  и state больше 90 МиБ (GitHub отклоняет файлы ≥ 100 МиБ).
- `state` — orphan-ветка; squash делает `force: true` **только** для неё.
  `main` force-push не применяется никогда.

Восстановление/просмотр вручную (нужен токен с `contents: read`):

```bash
export GH_TOKEN=... GITHUB_REPOSITORY=donjon1212322/github-open-source
python tools/state_branch.py read --out /tmp/live-state
python tools/state_branch.py status
```

Откат состояния: `git log` по ветке `state` (не по `main`), затем
`python tools/state_branch.py squash` после ручного восстановления файлов.
Осторожно: откат возвращает проекты в очередь и они могут выйти повторно.

### 4.3 dev.to: отдельные посты, а не серия

dev.to объединяет в серию все статьи с одинаковым полем `series`. Раньше
`publish_article` всегда передавал `"Open Source Finds"`, поэтому ежедневные
посты складывались в один блок «Part N Series». По решению владельца от
03.11.2026 серия упразднена:

- `POST /api/articles` **не содержит** `series` вообще;
- `PUT /api/articles/{id}` содержит `"series": null` — по документации Forem
  это снимает статью с серии; срабатывает при каждом `article-update`;
- уже опубликованные статьи (включая унаследованную `…-4774`) снимаются
  командой `Publishing pipeline → article-unseries` с input `article_ids`
  (только цифры и запятые; id из `state` добавляются автоматически).
  Команда делает минимальный `PUT {"article":{"published":true,"series":null}}`,
  не перерисовывая тело и обложку.

Проверка: в HTML статьи не должно быть `series-switcher` / `Part Series`,
а в профиле — счётчика `Series`.

### 4.4 Если пришло письмо «CodeQL configuration error»

В `codeql.yml` шаги `github/codeql-action/init` и `/analyze` **обязаны**
использовать один и тот же 40-символьный SHA. Dependabot обновлял их разными
PR и ломал анализ; теперь конфиг Dependabot удалён, а контрактный тест
`tests/test_codeql_pins.py` проверяет равенство SHA. Обновлять оба шага нужно
одновременно, в одном коммите.

### 4.5 Баннер «state had recent pushes … / Compare & pull request»

На главной странице репозитория GitHub автоматически показывает жёлтый баннер
для любой не-дефолтной ветки с недавним коммитом. Ветка `state` обновляется
при каждом реальном изменении состояния (`commit-state` пишет только когда
дайджест `state/state.json` изменился), поэтому баннер будет появляться
регулярно. **Это косметика GitHub, не запрос на действие:**

- **не нажимайте** «Compare & pull request» и **не сливайте** `state` в `main`;
- ветка `state` — orphan с **другой историей** (`compare/main...state` →
  `No common ancestor`), у неё нет общего предка с `main`, поэтому обычный
  merge в принципе невозможен;
- `main` должен оставаться code-only: `state/state.json` = bootstrap 181 B,
  `state/events.jsonl` = 0 B. Живой state (~143 КБ) живёт только в `state`;
- случайно созданный PR просто **закройте без слияния**; защита `protect-main`
  и CI-шаг `check-bootstrap` не дадут живому state оказаться в `main`;
- `2 Branches / 0 Tags` на скриншоте — ожидаемо: `main` + `state`, теги не
  используются.

Если баннер мешает визуально — его можно просто игнорировать: он исчезает сам,
а при следующем изменении состояния может появиться снова. Это нормальный
режим работы, ручных действий он не требует.

---

## 5. Откат

1. Отключить `Publishing pipeline` и `Doctor`.
2. Откатить три Variables обратно в `test` / `test` / пустой main ID, либо
   оставить всё выключенным.
3. При необходимости откатить последний коммит state через `git revert` —
   код и state лежат в одном репозитории, поэтому откат восстанавливает оба.
4. Проверить, что ни один cron не остался активным.

---

## 6. Безопасность

- Setup PAT — короткоживущий, отзывается сразу после настройки.
- Значения Secrets не читаются, не выводятся в логи и не коммитятся.
- GitHub API внутри workflow использует встроенный `github.token`
  (`GH_API_TOKEN` / `GH_TOKEN`); отдельный PAT в Secrets не требуется.
- Ключи, побывавшие в переписке, ротируются после запуска.

См. [`SECURITY.md`](../SECURITY.md), [`SECRETS_CHECKLIST.md`](SECRETS_CHECKLIST.md),
[`ARCHITECTURE.md`](ARCHITECTURE.md).
