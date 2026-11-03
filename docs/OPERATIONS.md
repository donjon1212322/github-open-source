# Эксплуатация

Что делать каждый день, что делать, когда сломалось, и как откатить.

---

## Ежедневно (2 минуты)

1. Открыть канал, посмотреть три поста.
2. Проверить, что превью отображается (значит ImgBB жив).
3. Если пост выглядит не так — `state/events.jsonl`, последние строки.

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
Затем пометить в состоянии:
```bash
PYTHONPATH=src python3 -c "
import json,pathlib
p=pathlib.Path('state/state.json'); d=json.loads(p.read_text())
d['published']['owner/repo']['removed_by_owner']=True
p.write_text(json.dumps(d,ensure_ascii=False,indent=1))
"
```

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
**С the current release владелец разрешил включать `Doctor` и `Publishing pipeline`.**
До переключения на `main` держим `APP_ENV=test` / `PUBLISH_TARGET=test`;
если workflow снова окажутся выключены после 60 дней без коммитов — включить
повторно (`Lifecycle guard` создаёт issue по этому поводу).

**Не запускайте ручные dispatch пачкой.** У `pipeline.yml` общая группа
`concurrency: state-writer` с `cancel-in-progress: false`: GitHub держит не
более одного выполняющегося и одного ожидающего запуска, поэтому из 10
одновременно отправленных команд 8 были `cancelled` (проверено the current release).
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

`state/state.json` в гите, история коммитов `chore(state):`.
```bash
git log --oneline -- state/state.json | head
git checkout <commit> -- state/state.json
```
Осторожно: откат вернёт проекты в очередь, и они могут выйти повторно.

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

## Безопасность (после аудита the current release)

- `pipeline.yml` разделён на два job: `run` (read-only, секреты, без
  `persist-credentials`) и `commit-state` (write, без секретов и без запуска
  кода проекта). Не сливайте их обратно в один job.
- `requirements.lock` обязателен: `pip install --require-hashes -r
  requirements.lock`. Не заменяйте его на `pip install -e .` в CI.
- `main` защищён ruleset: force-push и удаление запрещены. State-коммиты
  делает `github-actions[bot]`; не меняйте это без обхода ruleset.
- Dependabot, CodeQL и gitleaks включены. Не отключайте их.
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

Перед запуском `run` делает `git fetch origin main` и `reset --hard FETCH_HEAD`,
поэтому отложенный запуск видит свежий state. Не убирайте этот шаг: без него
возможна повторная публикация.


---

## Мониторинг и долгосрочная эксплуатация (с the current release)

| Сигнал | Где смотреть | Действие |
|---|---|---|
| `state.json` > 10 МиБ | шаг «State size guard» в CI, `Lifecycle guard` | проверить компактирование, при необходимости архивировать полные записи |
| Нет коммитов > 45 дней | `Lifecycle guard` | коммит/issue создаёт активность; иначе через 60 дней GitHub отключит все schedule |
| `GEMINI_MODEL` исчез из API | `doctor` → `Gemini_models`, `Lifecycle guard` | сменить Variable на актуальную модель; проект fail-closed и молча перестанет публиковать |
| Рост `Publish to Platforms` ошибок | Actions the previous bot/v6 | разобрать до включения флагов Bluesky |
| Приближение к квоте артефактов | Billing → Usage, Budgets and alerts | 64 артефакта/день × размер state; триггер — размер, не число запусков |

Полный разбор: the operations runbook.
