# Архитектура

Как система устроена внутри. Для тех, кому мало [`HANDOFF.md`](HANDOFF.md).

---

## Общая схема

```
                 ┌──────────────── СБОР ────────────────┐
GitHub Search ───┤  build_search_queries()               │
Hacker News ─────┤  → collect_candidates()               │
Telegram web ────┤  → merge_candidate() (дедуп по repo)  │
                 └───────────────┬──────────────────────┘
                                 ↓
                 ┌─────────── ОБОГАЩЕНИЕ ───────────────┐
                 │  enrich_candidates()                  │
                 │  звёзды, лицензия, contributors,      │
                 │  README (14 КБ), media_urls           │
                 └───────────────┬──────────────────────┘
                                 ↓
                 ┌──────────── ГОУТ ─────────────────────┐
                 │  gate.evaluate()                      │
                 │  лицензия · активность · сообщество · │
                 │  README · релевантность · блоклист    │
                 └───────────────┬──────────────────────┘
                                 ↓
                 ┌─────────── РАНЖИРОВАНИЕ ──────────────┐
                 │  rank.rank_candidates()               │
                 │  score + diversity_bonus + value_model│
                 └───────────────┬──────────────────────┘
                                 ↓
                        state.json → queue (кап 40)
```

Каждый день в 04:20 UTC очередь пополняется. Дальше три раза в день
`publish_slot` берёт верхний проект.

---

## Один пост: пошагово

`pipeline.publish_slot()` — 221 строка, самая сложная функция.

```
1. Проверка лимита дня          MAX_PROJECT_POSTS_PER_DAY = 3
2. select_candidate()           очередь + опубликованные сегодня + модель ценности
3. enrich_candidate()           свежие данные GitHub
4. dedup.check()                не публиковали ли раньше
5. generate_project_content()   ← вызов Gemini №1 (бриф)
                                  фолбэк: deterministic_project_content()
6. generate_surfaces()          ← вызов Gemini №2 (12 полей площадок)
7. choose_visual()              README → карточка → OpenGraph
8. _host_local_image()          ImgBB (картинка README тоже перезаливается)
9. render_project_card()        1200×630, если карточка
10. Telegraph.create_page()     статья на каждый пост
11. format_project_post()       HTML длинного поста
12. telegram.send_project()     текст + link_preview + кнопки
13. state.record_published()    запись + mark_seen
```

**Порядок важен:** запись в состояние идёт **после** отправки. Если процесс
упадёт между отправкой и записью — пост уйдёт, но в состоянии его не будет.
Это принятый риск: обратный порядок (запись → отправка) при падении
отправки потерял бы проект навсегда.

---

## Генерация: два вызова, не десять

### Вызов 1 — бриф проекта

Возвращает: `headline`, `hook`, `bullets`, `what_stands_out`, `why_matters`,
`article_markdown`, `article_title`, `category`, `seo_title`,
`seo_description`.

### Вызов 2 — поверхности

Схема `SURFACES_SCHEMA`, **12 полей**:

| Поле | Назначение | Лимит |
|---|---|---|
| `bluesky_headline` | заголовок Bluesky | 90 |
| `bluesky_lead` | подводка | 160 |
| `facebook_headline` | заголовок Facebook | 100 |
| `facebook_lead` | подводка | 260 |
| `Facebook_body` | **своё тело**, 120–200 слов | 2 600 |
| `devto_body` | **своя статья**, 450–650 слов Markdown | 9 000 |
| `telegraph_angle` | свой угол для Telegraph | 400 |
| `seo_query` | запрос, под который пишем | 70 |
| `seo_title` | заголовок статьи | 120 |
| `seo_description` | описание | 200 |

**У каждого поля свой лимит.** Общий лимит на все поля обрезал статью
посередине слова — это был реальный баг.

### Почему не вызов на площадку

Пять независимых писателей, читающих один README, расходятся в фактах.
утверждения — именно потому, что бриф был общим. Разные тела *поверх*
общего брифа дают разный голос без расхождения фактов.

**Стоимость:** 8 296 токенов и 2 вызова на пост. 24 888 токенов в день
при трёх постах. 27 запросов в день из 1000.

### Фолбэк

`deterministic_project_content()` строит пост из данных репозитория без ИИ.
Пост выйдет беднее, но выйдет. Проверено: все четыре адаптера дают
непустой текст (dev.to 2303, Facebook 1537, Bluesky 278).

---

## Визуал

`media.choose_visual()`:

```
1. media_urls из README      скоринг: .gif +40, demo/screenshot/hero +15,
                              logo/icon/avatar −30. Валидация: Pillow,
                              ≤8 МБ, реальный image/*
2. render_project_card()      1200×630, Editorial Daily / Neon Terminal
3. opengraph.githubassets.com  последний резерв
```

**Хостинг:** `_host_local_image()` → ImgBB → Telegraph (резерв).
GitHub release assets **исключены**: отдают `application/octet-stream`
с `content-disposition: attachment`, и Telegram молча выбрасывает превью.

Картинка из README тоже скачивается, сжимается (`shrink_for_web_preview`)
и **перезаливается на ImgBB**, чтобы пост не зависел от чужого хостинга.

---

## Кросс-посты

`pipeline.process_crossposts()` вызывается каждые 30 минут.

```
1. Найти посты, у которых crossposts.*.status == "pending" и due_at прошёл
2. best_of_day(): выбрать ОДИН лучший пост за последние сутки по score
3. Победитель → Bluesky (Facebook — отдельно, через make.com)
4. Остальные → status "held", reason "not_best_of_day"
5. Telegraph — отдельно, на каждый пост, не через эту очередь
```

Ретраи и dead-letter на каждую площадку. Фича-флаги: `FEATURE_BLUESKY`,
`FEATURE_DEVTO`. Facebook не в этой очереди:
`FEATURE_SHEETS_HANDOFF` пишет строку в Google Sheet, откуда публикует
make.com. Прямого Graph API в v6 нет.

---

## AI Brief

`pipeline.news_command()`, ежедневно 16:10 UTC.

```
1. fetch_worldofai_entries()      RSS канала WorldofAI
2. prefilter_entry()              дешёвые фильтры, см. ниже
3. extract_primary_urls()         ссылки из описания, по домену
4. fetch_primary_text()           читаем первоисточник (необязательно)
5. synthesize_brief()             ← Gemini, с видео или только текст
6. render_ai_brief_card()         Glass Aurora
7. send_text() + кнопки           «Official source», «Watch on WorldofAI»
```

### Префильтр — что и почему

| Фильтр | Правило |
|---|---|
| `outside_72h` | старше 72 часов — не новость |
| `video_seen` | уже публиковали этот ролик |
| `sponsored` | **но**: если продукт из спонсорской строки есть в заголовке — это запуск |
| `leak_or_rumor_title` | `leak`, `rumor` — не новость |
| `rumour_not_news` | `banned`, `drama`, `exposed` |
| `not_a_review_or_launch` | `top 10`, `tier list`, `i tested` |
| `reaction_content` | кликбейт **и** ни одного названного продукта |
| `not_developer_relevant` | нет технических терминов |

**Ловушка:** этот канал пишет `HUGE`/`INSANE`/`BOMBSHELL` в 11 заголовках
из 15, включая настоящие запуски. Поэтому реакция определяется **отсутствием
названного продукта**, а не прилагательным. Суть оценивает ИИ, и он
справляется: на «GPT-6 Is INSANE» без бенчмарков ответил отказом.

---

## Состояние

`state/state.json` — единый JSON, коммитится после каждого запуска.

| Ключ | Что |
|---|---|
| `queue` | кандидаты, кап 40 |
| `published` | одна запись на пост: `generated`, `crossposts`, `articles`, `value_score`, `views` |
| `seen_repos` | что уже видели и с каким решением |
| `news_topics` | опубликованные брифы |
| `last_runs` | последний запуск каждой команды |
| `dead_letters` | сбои, требующие внимания |
| `value_model` | множители ранжирования |

`state/events.jsonl` — журнал. Коммитится, поэтому **ротация обязательна**:
5000 строк → хвост 2000.

---

## Предохранители

| Что | Где | Как работает |
|---|---|---|
| Целевой канал | `Config.telegram_chat_id` | `production`+`main` → основной; иначе тестовый; **если тестовый не задан — падает**, не откатывается на основной |
| Лимит постов | `publish_slot` | `MAX_PROJECT_POSTS_PER_DAY = 3`, до вызова Gemini |
| Дедупликация | `dedup.check` | отпечатки описания и README, осознанные возвраты по вехам |
| Секреты в ошибках | `http.sanitize` | значения вычищаются из текста исключений |
| Сканирование | `tools/secret_scan.py` | в CI, падает на похожем на ключ |
| Слияние состояния | `concurrency: state-writer` | кроны не перезаписывают друг друга |

---

## Тесты

188 тестов, `pytest -q`, ~2 секунды.

| Файл | Тестов | О чём |
|---|---|---|
| `test_value_system.py` | 46 | правила владельца, форматы, лимиты, кросс-посты |
| `test_gate_formatter.py` | 27 | гоут и форматтер |
| `test_media_host.py` | 9 | ImgBB, Telegraph, сжатие |
| `test_content_adapters.py` | 7 | тексты под площадки |
| `test_state_sources.py` | 6 | состояние и источники |
| `test_telegram_payload.py` | 2 | payload и кнопки |
| `test_news_format.py` | 1 | формат брифа |

**Не покрыты тестами:** `cli`, `doctor`, `http`, `upload` —
проверены живыми прогонами, см. the architecture tests.
