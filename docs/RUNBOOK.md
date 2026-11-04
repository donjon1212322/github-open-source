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

### 4.6 AI Brief: если YouTube-RSS отдаёт 404

`https://www.youtube.com/feeds/videos.xml?channel_id=...` периодически
отдаёт 404/500 **для всех каналов** (включая каналы самого Google); это
известные волны отказов 2021/2024/2025/2026 годов, а не ошибка нашего
`CHANNEL_ID`. Порядок источников в `news.py`:

1. RSS — основной, один запрос;
2. при 404/5xx — страница `youtube.com/@intheworldofai/videos`
   (`lockupViewModel` → `contentId`, заголовок, относительный возраст);
3. для 5 свежих роликов — страница просмотра, откуда берутся
   `shortDescription` и точная `publishDate` (нужны редактурному фильтру).

Если недоступны оба источника — это **не падение workflow**: команда пишет в
state `news_skipped / source_unavailable`, один раз уведомляет владельца и
возвращает `ok: true`. Красный run и письмо об ошибке в этом случае больше не
появляются. Проверить вручную: `Actions → Publishing pipeline → news`.

### 4.7 Карточка поста и дефолтная карточка GitHub

ImgBB иногда отвечает transient `503`, а `telegra.ph/upload` — `400 Unknown
error`. Раньше в этом случае `image_url` подменялся на
`opengraph.githubassets.com/1/<repo>`, и подписчик видел стандартную карточку
GitHub вместо нашей. По решению владельца от 04.11.2026:

- `ImgBBMediaHost` повторяет 429/5xx/не-JSON до 4 раз с backoff 1/2/4 c;
- при полном отказе хоста `image_url` остаётся пустым, превью берётся из
  нашей страницы Telegraph, а если её нет — превью отключается вовсе;
- подмена на `opengraph.githubassets.com` запрещена контрактным тестом.

### 4.8 dev.to: одна статья в сутки и без дублей репозитория

03.11.2026 вышло 3 статьи, две из них — про `horizon-rust/horizon`: старая
`…-4774` из домиграционного репозитория и новая `…-3djc`. Причины:
`article_of_day` обходил **все** посты дня (ручной запуск + cron давали две
статьи) и не знал о статьях того же аккаунта dev.to вне нашего state.

Теперь: не более одной статьи за UTC-день (проверка
`articles.devto.published_at`), а перед POST — сверка с
`GET /api/articles/me/all` по ссылке `https://github.com/<owner>/<repo>` в
`body_markdown`. Найден дубль → `article_skipped_duplicate`, статья не
создаётся, URL существующей сохраняется в `articles.devto_duplicate`.

### 4.9 Комментарии: выбран вариант A (реализовано 04.11.2026)

Telegram показывает нативную кнопку «Leave a comment» только у постов с
привязанной группой обсуждений **и без собственного `reply_markup`**. Наши
посты несли строку inline-кнопок (GitHub / Full breakdown / Homepage), поэтому
кнопка комментариев пропадала — это ограничение клиента, а не баг бота.

Владелец выбрал **вариант A**: `TelegramClient.send_project()` по умолчанию
не передаёт клавиатуру, `news_command` тоже шлёт пост без `buttons=`. Ссылки
на GitHub, Telegraph и homepage остаются в тексте поста, поэтому контент не
теряется. `include_buttons=True` оставлен как явный opt-in на особый случай.
Нужна привязанная группа обсуждений в настройках канала — Bot API не умеет
её создавать; `doctor` теперь сообщает `telegram_discussion.linked_chat_id`.

### 4.10 AI Brief: дайджест всего ролика без рекламы и без источников

Правила владельца от 04.11.2026 (первая и вторая правка), закодированы в
`select_latest_news_entry`, `NEWS_SCHEMA`, промпте `synthesize_brief` и
`format_ai_brief`:

* берётся **только самый новый** ролик канала, а не обход всех свежих;
* свежесть — сегодня или вчера (`published.date() >= today - 1 day`);
* дедупликация по `video_id` и `topic_key` за 14 дней из `news_topics`;
* **это дайджест всего ролика, а не одна заметка.** Схема ответа содержит
  `stories[]` (4–7 разных сюжетов, у каждого `title`, `models[]`,
  `what_happened[]`, `why_it_matters`, `confirmed`). `[Time Stamps]` и `Tags`
  из описания служат оглавлением. Один сюжет = отказ `not_a_digest`;
* **новости про модели идут первыми.** Корпоративные/IPO/policy-сюжеты — максимум
  один, и только в конце. Первый выпуск ошибочно сжал 33-минутный round-up до
  одной строки про RSI — это и есть причина второй правки;
* **технические названия неизменны**: `Claude Fable 5.5`, `Claude Opus 5.5`,
  `GPT-6 Astra`, `Qwen 4 27B`, `Gemini 4 Argon`, `DeepSeek Harness`,
  `LongCat-2.5`, `Pulse Alpha`, `Kentia` копируются дословно, с точками,
  дефисами, цифрами и регистром. `_protect_names` предохраняет их от
  `clean_brief_headline` и обрезки; отдельная строка «🧬 In this brief:»
  перечисляет точные имена;
* Gemini обязан **вырезать всю рекламу**: «Sponsored by», промокоды,
  партнёрские ссылки, скидки и призывы подписаться; обычная спонсорская
  вставка не является причиной отклонить ролик;
* **источник не указывается вообще**: нет секции Sources, нет ссылки на
  YouTube, нет «Video-only»/«WorldofAI»/«SPOTTED VIA», нет link-preview с
  URL ролика. Неподтверждённые утверждения помечаются «Unconfirmed».
  `primary_source` используется только для внутренней проверки фактов;
* вёрстка: пронумерованные сюжеты `1️⃣…`, по одной мысли на строке,
  «Why it matters» у каждого сюжета, общий «Why developers should care»,
  Receipts, «Your take?», `React`, подпись канала; лимит 3350 видимых /
  3920 сырых UTF-16 знаков; при переполнении отбрасываются целые сюжеты с
  хвоста, а не обрезаются названия моделей.

Исправление уже вышедшего поста: dispatch `news` с `refresh_news=true` →
`ghos-v6 news --refresh`. Команда обходит дедуп (но **не** проверку свежести),
пересобирает дайджест и вызывает `editMessageText` для того же
`telegram_message_id`, обновляя запись в `news_topics`. Дубль поста не
создаётся. Штатный cron и обычный dispatch по-прежнему дедуплицируют.

### 4.11 Удаление дубля dev.to

Команда `devto-remove --ids` (dispatch `devto-remove`, input `article_ids`)
пробует по цепочке `DELETE /api/articles/{id}` →
`PUT /api/articles/{id}/unpublish` (требует `super_moderator`, обычный ключ
получает 401) → `PUT /api/articles/{id}` с `published:false`, что переводит
статью в приватный черновик и убирает её из публичной ленты. **Защита:** id, на который ссылается
`published[].articles.devto`, удалить нельзя — он попадает в `blocked`.

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

### 4.11 Третья правка AI Brief: чёткая карточка, живой хук и статусы фактов

Отклонение владельца 04.11.2026 по скрину `uploads/image-10.png`: «буквы
замылены, мелкий текст кривой и мыльный, наезжает на рамки», «все сухо
расказывается, не интересно», «почему ты написала что Claude Fable 5.5 уже
вышел? … тоже самое и с остальными фактами, перепроверь».

**Карточка** (`render_ai_brief_card`, `cards.py`). Причины брака были
детерминированными: `_wrap(..., max_lines=3)` молча обрезал заголовок
(потерялось «GPT Updates»); standfirst из трёх строк с `y≈357, step 38`
доходил до `≈471` и перекрывал stat bar `455..535`; три колонки шириной
430/250/200 с `_fit(..., minimum=15)` ужимались до 15–17 px и наезжали друг
на друга и на рамку. Теперь:

* `ai_brief_card_geometry()` — чистая функция, фиксированные непересекающиеся
  полосы: заголовок от `y=140`, `_fit_block` 58→36 px до 3 строк **без обрезки
  слов**, хук от `head_bottom + 22`, stat band `470..568`; если хук не
  помещается в две строки, берётся одна строка 26→22 px, а не втискивание;
* `stand_bottom <= band_top - 8` проверяется тестом для длинных заголовков;
* `ai_brief_card_columns()` — **две** широкие колонки `(116, 620)` и
  `(772, 320)`, `left_end < right_x`, короткие значения (первые два имени,
  «N stories · ad-free»), минимум шрифта 21–22 px. Ни один текст не
  уменьшается до 15 px и не вылезает за рамку;
* stat band залит непрозрачным тёмным фоном — белый текст читается на любом
  градиенте; на карточке нет атрибуции источника.

**Подача.** В схеме появилось обязательное поле `hook`, в промпте — блок
`MAKE IT INTERESTING`: одна фраза с конкретным числом/контрастом/
неразрешённым противоречием, запрет «This digest covers recent AI releases
and updates…», «Why it matters» заменён на короткое «So what» с конкретным
действием. `hook` (а не сухой `summary`) уходит и в текст, и на карточку;
`news_command` передаёт в карточку ещё и число сюжетов.

**Статусы фактов — главное исправление.** Перепроверка 04.11 показала, что
`Claude Fable 5.5` **не выпущен**: вторичные источники на 02.11 сообщают
«not released / no date», актуальная модель `claude-fable-5-1`, а ролик
обсуждает «New Fable 5.5 Checkpoint», «Fable 5.5 Release Date?» и утечки
Azure/LiteLLM/партнёрского доступа. Прошлая карточка и заголовок писали
«Released» — это фактическая ошибка. Аналогично: `LongCat-2.5` официально
«introduced» с Try it/API Access, но `GitHub/HF Coming soon` (веса не
выложены); `GPT-6 Astra` — исследовательский отчёт на одном
`Qwen3.5-0.8B` (`7.9→48.1` MATH-500, `0.016 %` весов, без обучения), не
продукт OpenAI; demos `DeepSeek Harness Creator Mode` часто требовали
несколько раундов промптов.

Механизм: у каждого сюжета обязательные `status` и `basis`. `status` принимает
`official_release`, `announced_not_shipped`, `research_report`,
`demo_or_benchmark`, `leaked_checkpoint`, `teaser`, `unconfirmed_rumour`,
`speculation`; `basis` — короткий **уровень доказательства** («leaker
screenshots, unverified», «researchers' own write-up, no independent
reproduction»), без имени канала, URL и списка источников. В тексте каждый
сюжет получает явный бейдж (`✅`, `📄`, `🔬`, `🎬`, `◻️`), раздел «Receipts»
показывает сводку статусов и строку «Only ✅ is an officially confirmed
launch». `enforce_claim_status()` — детерминированный предохранитель: для
всех статусов кроме `official_release` глаголы `released / launched / shipped /
introduced / added / is out / now available` заменяются на `reported /
announced / shown`, а `confirmed` принудительно `False`. Сырой массив
`confirmed` больше не рендерится — он был источником ложного «✅» рядом с
неподтверждённым утверждением. Двойное «Unconfirmed: Unconfirmed …
(unconfirmed)» чистятся `_clean_unconfirmed()`.

**Тесты** `tests/test_news_accuracy_and_card_2026_11_04.py` (19 тестов)
кодируют: непересекающиеся полосы и сохранение всех слов заголовка, две
непересекающиеся колонки и шрифт ≥ 22 px, обязательные `hook/status/basis`
в схеме, маркеры проверки фактов в промпте, даунгрейд «Released» для
`leaked_checkpoint`, сохранение глаголов для `official_release`, «Announced»
для `announced_not_shipped`, наличие хука/«So what»/сводки статусов,
отсутствие атрибуции источника и передачу `hook` + `stories` в карточку.
15 новых мутаций (всего 81/81) убивают возврат к каждой из трёх ошибок.

**Правка живого поста.** Обычный `news` не запускать: `video_id=i-wYjUriSzo`
уже в `news_topics` и dedup вернёт `news_already_published`. Только
`news --refresh` / `refresh_news=true` пересобирает дайджест и правит тот же
`message_id=3553` через `editMessageText`; новый пост не создаётся.

### 4.12 Четвёртая итерация той же правки: аудит статусов и живой хук

Первый live-refresh третьей правки (`41775169118`, `edited:true`,
`message_id=3553`) убрал «Claude Fable 5.5 Released», но показал две
остаточные ошибки того же класса:

* Gemini пометил **три** сюжета как `✅ Official release · official model
  card` — включая `Anthropic amends S-1 filing...` (политика/документ, не
  релиз продукта), demo `DeepSeek Harness Creator Mode` и `LongCat-2.5`;
* `hook` оказался сухим пересказом последнего корпоративного сюжета
  («Anthropic's amended S-1 disclosures suggest...»), а заголовок карточки
  открывался словами «Anthropic S-1 filings reveal RSI milestone» —
  нарушение «model news first».

Добавлен детерминированный аудит, не зависящий от самооценки модели:

* `_audit_status()` читает `title`, `what_happened` и `basis` сюжета.
  `official_release` с доказательствами `checkpoint/leak/partner access`
  понижается до `leaked_checkpoint`; `S-1/SEC/filing/IPO/policy` — до нового
  статуса `official_document` (`📄 Official filing · not a product launch`);
  `Coming soon/not shipped` — до `announced_not_shipped`; `open beta/demo/
  multi-round/X post` — до `demo_or_benchmark`; `researchers/write-up` — до
  `research_report`. Ложный `basis` «official model card» заменяется на
  честную формулировку уровня доказательства;
* «Receipts» теперь считает `official_document` и `announced_not_shipped`
  в группе `📄`, а не в `✅`, и печатает `Only ✅ is an officially confirmed
  launch`;
* `editorial_hook()` отклоняет хук, если он равен `summary`, начинается с
  «This digest/brief...» или с корпоративного/политического сюжета, и не
  содержит ни числа, ни точного имени, ни вопроса. Вместо него собирается
  фактический тизер из подсчитанных статусов и точных имён: «N exact names,
  K confirmed launches, M claims still open. Start with <имя> — then decide
  what you would actually re-run.» Ни один факт не выдумывается;
* `_model_first_headline()` заменяет заголовок, открывающийся на `S-1/IPO/
  RSI/filings/policy`, на модельную связку из первых трёх точных имён;
* `enforce_claim_status()` нормализует `headline`, `hook` и `card_hook`
  до рендера, поэтому текст поста и карточка всегда согласованы.

Промпт получил явные примеры маппинга глав на статусы, правило «WHEN IN
DOUBT, USE THE LOWER STATUS» и запрет открывать хук/заголовок корпоративным
сюжетом. Тесты (19 в файле) кодируют понижение S-1/demo/leak/research,
честную сводку Receipts, замену сухого хука и модельный заголовок; 15 новых
мутаций (81/81) убивают возврат каждой ошибки.
