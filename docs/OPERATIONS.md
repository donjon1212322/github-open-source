# Эксплуатация

Что делать каждый день, что делать, когда сломалось, и как откатить.

> **State.** Живой `state/state.json` и `state/events.jsonl` лежат в
> orphan-ветке `state`, а не в `main`. Перед любой локальной командой ниже
> загрузите их: `python tools/state_branch.py read` (нужен `GH_TOKEN`).
> Файлы `state/` в `main` — пустой bootstrap; править их бессмысленно,
> изменения всё равно придут CAS-записью из workflow.

---

## Ежедневно (2 минуты)

1. Открыть канал, посмотреть три поста.
2. Проверить, что превью отображается (значит ImgBB жив).
3. Если пост выглядит не так — `tools/state_branch.py read`, затем
   `state/events.jsonl`, последние строки.

Ничего запускать руками не нужно: 13 кронов делают всё сами.

---

## Еженедельно

| Когда | Что смотреть |
|---|---|
| Вс 16:10 | дайджест топ-5 и опрос — открылись? |
| Чт 18:10 | опрос закрылся, `learn` пересчитал модель |
| Вс 16:40 | отчёт владельцу пришёл в личку |
| Любой день | `value_model` в `state.json` — не выродился ли |

---

## Диагностика

### Первым делом

```bash
set -a; . .env.local; set +a
PYTHONPATH=src python3 -m ghos_v6.cli doctor
```

13 проверок, ничего не меняют. Если что-то отвалилось — видно сразу.

| Проверка | Что ловит |
|---|---|
| `env_presence` | не хватает переменной |
| `telegram_get_me` | токен бота мёртв |
| `telegram_discovery` | бот не админ канала |
| `Gemini_models` / `Gemini_generate` | квота или ключ |
| `GitHub_search` | токен GitHub |
| `worldofai_rss` | канал сменил RSS |
| `telegraph`, `devto`, `bluesky`, `Facebook` | токены площадок |
| `card_render` | Pillow или шрифты |

### Частые поломки

**Пост не вышел.**
```bash
python tools/state_branch.py read
tail -20 state/events.jsonl | grep publish
```
`no_candidate` → очередь пуста, `collect` не отработал.
`daily_limit_reached` → норма, лимит 3.
`all_candidates_rejected` → гоут отсёк всех, смотреть `rejections`.

**Превью не отображается.**
Проверить `IMGBB_API_KEY`. Если пуст — карточка не загрузится, пост уйдёт
без превью с записью в лог. Telegraph — резерв, но он хуже.

**AI Brief молчит.**
```bash
PYTHONPATH=src python3 -m ghos_v6.cli news --force
```
В ответе `rejections` — по каждой причине. Если `sponsored` — проверить,
не изменился ли формат спонсорской строки у канала.

**Кросс-посты не уходят.**
Проверить `FEATURE_BLUESKY`. Для Facebook —
`FEATURE_SHEETS_HANDOFF` и наличие `GOOGLE_SERVICE_ACCOUNT_JSON`; Page-токен
не нужен и в v6 не читается.
Потом `crossposts.*.status` в записи поста: `pending` → `sent` или
`dead_letter`.

**Дубликат в канале.**
`dedup.check` пропустил. Смотреть `seen_repos` по этому проекту.
Причина обычно — изменился README и отпечаток не совпал.

---

## Инциденты

### Опубликован плохой пост

Telegram-канал: удалить сообщение руками. Бот не умеет отзывать.
Затем запустить `Publishing pipeline → moderation` (или `report`) — состояние
запишется в ветку `state` штатным CAS-коммитом. **Не правьте bootstrap в
`main`**: workflow его всё равно перезапишет при чтении, а CI запретит
появление там реальных записей.

### Утёк секрет

1. Отозвать токен у провайдера **сразу**.
2. Заменить в Settings → Secrets.
3. `python3 tools/secret_scan.py` — проверить репозиторий.
4. Секреты никогда не печатать в чат.

### Публикация ушла не в тот канал

`Config.telegram_chat_id` не откатывается молча на основной. Если это
случилось — значит `TELEGRAM_TEST_CHANNEL_ID` был пуст, и код должен был
упасть, но не упал. Проверить `config.py`.

### GitHub Actions не запускаются

Проверить, не `disabled_manually`:
```bash
gh api repos/{owner}/{repo}/actions/workflows
```
**С 03.11.2026 владелец разрешил включать `Doctor` и `Publishing pipeline`.**
До переключения на `main` держим `APP_ENV=test` / `PUBLISH_TARGET=test`;
если workflow снова окажутся выключены после 60 дней без коммитов — включить
повторно (`Lifecycle guard` создаёт issue по этому поводу).

**Не запускайте ручные dispatch пачкой.** У `pipeline.yml` общая группа
`concurrency: state-writer` с `cancel-in-progress: false`: GitHub держит не
более одного выполняющегося и одного ожидающего запуска, поэтому из 10
одновременно отправленных команд 8 были `cancelled` (проверено 03.11.2026).
Отправляйте по одной и дожидайтесь завершения; штатные cron не конфликтуют,
потому что идут по расписанию.

---

## Откат

### Откатить пост-формат

Форматтер — `formatter.py::format_project_post`. Вернуть прошлую версию:
```bash
git log --oneline -- src/ghos_v6/formatter.py
git revert <commit>
```

### Откатить всю публикацию

```bash
APP_ENV=test
PUBLISH_TARGET=test
```
Публикация в основной канал прекратится немедленно, тестовый продолжит
работать. Это штатный способ.

### Откатить состояние

Живой state лежит в orphan-ветке `state`, история коммитов `chore(state):`.
```bash
git fetch origin state
git log --oneline FETCH_HEAD -- state/state.json | head
git show FETCH_HEAD:state/state.json | head
export GH_TOKEN=... GITHUB_REPOSITORY=donjon1212322/github-open-source
python tools/state_branch.py read --out /tmp/live-state   # текущее состояние
python tools/state_branch.py status                        # размер, свежесть, main
```
Восстановление файла делается через `tools/state_branch.py squash` после
ручной правки в `/tmp/live-state`; `main` при этом не трогается. Осторожно:
откат вернёт проекты в очередь, и они могут выйти повторно.

---

## Продление и лимиты

| Ресурс | Лимит | Расход | Запас |
|---|---|---|---|
| Gemini | 1000 запросов/день | 27 | 37× |
| GitHub API | 5000/час | ~100 на сбор | большой |
| ImgBB | без жёсткого лимита | 3–4 загрузки/день | — |
| Telegram | 30 сообщений/сек | 3/день | — |

Если Gemini начнёт упираться — первым делом сокращать не посты, а вызов
поверхностей (сделать его раз в день на лучший пост).

---

## Что делать НЕЛЬЗЯ

- Включать `pipeline.yml` / `doctor.yml` на тестовом аккаунте.
- Печатать значения секретов в логи или чат.
- Менять `APP_ENV`/`PUBLISH_TARGET` без прохождения приёмки из `RUNBOOK.md`.
- Править `state/state.json` руками при работающих кронах — перезапишут.
- Отключать `concurrency` в workflow — кроны начнут перезаписывать состояние.

---

## Безопасность (после аудита 03.11.2026)

- `pipeline.yml` разделён на два job: `run` (read-only, секреты, без
  `persist-credentials`) и `commit-state` (write, без секретов и без запуска
  кода проекта). Не сливайте их обратно в один job.
- `requirements.lock` обязателен: `pip install --require-hashes -r
  requirements.lock`. Не заменяйте его на `pip install -e .` в CI.
- `main` защищён ruleset: force-push и удаление запрещены. Единственный
  force-update во всём проекте — ежемесячный `squash` ветки `state`.
- CodeQL и gitleaks включены. Dependabot version updates **отключены**
  (`.github/dependabot.yml` удалён 03.11.2026): его PR меняли закреплённые SHA
  экшенов и `requirements-dev.in` без пересборки хэш-локов, поэтому были
  несливаемым шумом. Dependabot **vulnerability alerts в Settings отключены**
  (не менялись), `automated-security-fixes=false` — значит Dependabot не
  создаёт здесь никаких PR и уведомлений. Secret scanning, push protection,
  CodeQL и gitleaks включены.
- При инциденте: `git revert` + новый релиз. Не правьте `state/state.json`
  руками при работающих кронах.

### Расписание после аудита №2

| Cron | Команда |
|---|---|
| `20 4 * * *` | collect |
| `10 7 * * *`, `40 12 * * *`, `15 17 * * *` | publish (3 поста в день) |
| `5,35 * * * *` | crossposts |
| `30 13 * * *` | article of the day |
| `10 19 * * *` | AI Brief |
| `45 */3 * * *` | moderation |
| `20 18 * * *` | Facebook handoff (make.com) |
| `10 16 * * 0`, `50 18 * * 4`, `20 20 * * *`, `40 16 * * 0` | digest / poll / metrics / report |

Перед запуском `run` делает `python tools/state_branch.py read`: отложенный
запуск видит свежий state с ветки `state`, хотя `main` чекаутится на
`fetch-depth: 1`. Не убирайте этот шаг: без него возможна повторная публикация.
Запись идёт CAS-коммитом в ту же ветку, поэтому гонка не теряет данные.


---

## Мониторинг и долгосрочная эксплуатация (с 03.11.2026)

| Сигнал | Где смотреть | Действие |
|---|---|---|
| `state.json` > 8 МиБ | `tools/state_branch.py status` в `Lifecycle guard` и `Doctor` | проверить компактирование, при необходимости архивировать полные записи; жёсткий отказ записи на 90 МиБ |
| Ветка `state` не менялась > 2 дней | heartbeat в `status` | `Lifecycle guard` создаёт issue; иначе через 60 дней GitHub отключит все schedule |
| state снова в `main` | CI `check-bootstrap`, `Doctor` | сборка красная: вернуть bootstrap и проверить, что `commit-state` пишет в ветку |
| `GEMINI_MODEL` исчез из API | `doctor` → `Gemini_models`, `Lifecycle guard` | сменить Variable на актуальную модель; проект fail-closed и молча перестанет публиковать |
| Рост ошибок кросс-постов | Actions → Publishing pipeline | разобрать `dead_letters`; Bluesky включён |
| Приближение к квоте артефактов | Billing → Usage, Budgets and alerts | артефакт создаётся только при изменении state (не на каждом из 64 запусков); триггер — размер, не число запусков |

Полный разбор: `docs/RUNBOOK.md` и отчёт о долгосрочных рисках.

### Письма об «ошибках» от Dependabot (03.11.2026)

Два письма вида `Re: [...] Bump github/codeql-action/analyze ... (PR #2)`
были **не поломкой продовой ветки**: Dependabot обновил `init` и `analyze`
двумя разными PR (#4 и #2), поэтому в `codeql.yml` на ветке PR оказались
версии 4.39.1 и 4.39.3 одновременно. CodeQL требует одинаковую версию у
всех своих шагов и падает с `configuration error`. Всего в истории репозитория
было ровно 2 failed-запуска, оба на ветках `dependabot/*`; на `main` оба шага
закреплены на одном SHA `793b778da9c9275a0f276522b27cb5fc14bba279`.

PR закрыты, ветки удалены, конфиг Dependabot удалён — новые такие письма не
придут. `tests/test_codeql_pins.py` и отдельная мутация запрещают рассинхрон:
если кто-то обновит только один из шагов, CI упадёт сразу, а не письмом на
почту. Проверить вручную: `Actions → CodeQL → Run workflow`.

### Баннер «state had recent pushes» на главной странице

GitHub сам показывает такой баннер для любой не-дефолтной ветки с недавним
пушем. `state` обновляется при изменениях состояния, поэтому баннер появляется
регулярно — **это не ошибка и не призыв слить PR**. Ветка `state` orphan, без
общего предка с `main`; слиять её нельзя и не нужно. См. `docs/RUNBOOK.md` §4.5.

### Инциденты 04.11.2026

- **AI Brief** не вышел из-за 404 YouTube-RSS; источник теперь двухуровневый,
  а недоступность источника даёт зелёный skip + уведомление владельца.
- **Карточка Mika** ушла в дефолт GitHub: ImgBB 503 + Telegraph 400. Добавлен
  retry, подмена на `opengraph.githubassets.com` убрана.
- **dev.to** получил 3 статьи за день и дубль по `horizon-rust/horizon`:
  добавлены лимит «1 статья в UTC-день» и сверка с уже опубликованными
  статьями аккаунта.
- **Комментарии** пропадали из-за inline-кнопок; владелец выбрал вариант A,
  кнопки убраны, см. `INCIDENTS_2026-11-04.html`.

### Решения владельца 04.11.2026 (реализованы)

- **Вариант A**: inline-клавиатура убрана из всех постов канала → возвращается
  нативная кнопка комментариев; ссылки остаются в тексте.
- **Старый дубль** dev.to `…-4774` (id `4968458`) удаляется командой
  `devto-remove`; живая статья `…-3djc`, на которую ссылается state, защищена.
- **AI Brief**: только последний ролик, свежесть ≤ вчера, дедуп по
  `video_id`, Gemini вырезает рекламу, пост свёрстан секциями.

### Живая проверка 04.11.2026 (все запуски по одному, `state-writer`)

| Запуск | Результат |
|---|---|
| `41765707131` Doctor | `telegram_discussion.ok:true`, `linked_chat_id -1003849113813`, группа «GitHub Open Source — Chat» |
| `41766192671` `devto-remove --ids 4968458` | `action: drafted`, `blocked: []`; `…-4774` и `/api/articles/4968458` → 404, `4971446` сохранена |
| `41766291380` `news` | `published:true`, сообщение **3553**, `video_id i-wYjUriSzo`, `Gemini_video_input:true` |

Первый `devto-remove` (`41765789330`) упал на `401 unauthorized`: Forem
 privilege-gate'ит `PUT /articles/{id}/unpublish` за `super_moderator`. Добавлен
третий fallback — `PUT /articles/{id}` с `published:false` (приватный черновик,
публично 404); коммит `32662d7`, запись о провальном запуске удалена из Actions.

В живом посте 3553: ноль `inline_button`, ноль рекламных маркеров, секции
`What shipped / Why developers should care / Receipts / Sources / Your take?`,
разделители и пустые строки. `news_topics` = 1 запись с `video_id` и
`telegram_message_id`; state `3cb2124fb56f`, `healthy:true`, `problems:[]`.

**Ограничение проверки:** нативную кнопку «Leave a comment» рисует клиент
Telegram, а не Bot API; с сервера её не видно. Предусловия ( linked supergroup +
отсутствие `reply_markup` ) подтверждены, визуальное подтверждение — за
владельцем в приложении. Если кнопки нет, следующий шаг — DiscussBot или
публикация от имени админа.

### Вторая правка AI Brief, 04.11.2026 (замечания владельца)

- Первый живой пост **3553** сжал весь round-up до одной новости про RSI:
  схема Gemini была рассчитана на один `headline`. Переделано на `stories[]`
  с 4–7 сюжетами; новости про модели идут первыми, policy — максимум один
  сюжет в конце. Один сюжет теперь отклоняется (`not_a_digest`).
- Точные технические названия моделей копируются дословно и защищены от
  чистки заголовков и обрезки; добавлена строка «🧬 In this brief:».
- Источник не указывается: удалены секция Sources, ссылка на YouTube,
  «Video-only», «WorldofAI» на карточке и link-preview с URL ролика.
- `news --refresh` / input `refresh_news` пересобирает дайджест и правит
  уже вышедший пост через `editMessageText` без создания дубля.
- Гейт: `391 passed`, `69.76 %`, `76/76 killed`, ruff/mypy/secret-scan/YAML
  чисто; третья правка поверх `39feb9c`, CI и CodeQL зелёные.
- Живые запуски (все `completed/success`, все правят один пост 3553 через
  `editMessageText`): `41769035382` → 3 сюжета/13 имён, `41769791800` →
  5 сюжетов/14 имён, `41770458748` → 4 сюжета/17 имён. Финальный пост:
  `Claude Fable 5.5`, `GPT-6 Astra`, `DeepSeek Harness`, `LongCat-2.5`,
  12 точных имён в «In this brief», 0 источников, 0 рекламы,
  0 inline-кнопок, дубля поста нет. YouTube-RSS отдавал 404 — сработал
  фолбэк на вкладку /videos.
