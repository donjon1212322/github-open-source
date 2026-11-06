# Внешний будильник для watchdog — ПОДКЛЮЧЁН 06.11.2026

**Статус: работает.** Worker **`ghos-v6-tick`** развёрнут на бесплатном аккаунте
Cloudflare (`sairusplay`), cron **`*/30 * * * *`** (каждые полчаса, UTC),
переменная `GH_REPO = donjon1212322/github-open-source`, секрет `GH_PAT`.
URL: `https://ghos-v6-tick.sairusplay.workers.dev`.

Первый **автоматический** тик — **15:30:24 Минск 06.11** → GitHub Actions run
`42218175231` (`event: repository_dispatch`), **success**: `checked: 6`,
`missed: []`, `dispatched: []`, `failed: []`, `alerts: []`, без публикаций.
Существующие приложения аккаунта (`ogonek-test`, `gpbot`) не тронуты.

## Зачем

GitHub `schedule` не гарантирует доставку. 06.11 не пришли запуски в 07:27,
09:27, 11:27, 13:27 по Минску, а после 10:27 не было ни одного запуска 3 ч
26 мин; штатный `collect` за 06.11 вообще пришёл лишь в **15:20** вместо 07:23.
Внешний будильник дёргает **API** (`repository_dispatch`), а не общую очередь
расписаний GitHub, поэтому создание запуска от GitHub-планировщика не зависит.

## Что сделано в репозитории

- `watchdog.yml` принимает `repository_dispatch` с `types: [watchdog_tick]`;
- вторая cron-строка `57 */2 * * *` как резервный тик;
- `queue: max` в общей группе `state-writer` не даёт отменить созданный запуск;
- watchdog подтверждает слот по state, повторяет не чаще 30 минут и больше не
  шлёт тревожное сообщение на первый успешный dispatch.

## Как развёрнуто (для восстановления)

```bash
cd tools/external_tick
cp wrangler.toml.example wrangler.toml   # вписать name, account_id, GH_REPO
npx wrangler deploy                      # cron из [triggers]
printf '%s' "$GH_PAT" | npx wrangler secret put GH_PAT
npx wrangler secret list                 # только имена, без значений
```

Официальные лимиты Workers Free: **5 Cron Triggers на аккаунт**,
**100 000 запросов/сутки**, 10 мс CPU. Наш один `*/30` = 48 вызовов/сутки.
Секрет в репозиторий не попадает; `wrangler secret put` кладёт его в Cloudflare.

## Важные уроки эксплуатации

- **После `wrangler deploy` расписание подхватывается не сразу** (несколько
  минут). При отладке 06.11 четыре передеплоя подряд всё время сбрасывали
  таймер — первые тики пришли только когда деплои прекратились. Не дёргайте
  прод без нужды.
- Cron в Cloudflare считается по UTC; `*/30` = :00 и :30 каждого часа UTC,
  то есть :00 и :30 по Минску тоже (UTC+3).
- Если **отозвать `GH_PAT`**, будильник встанет. Сначала замените секрет, потом
  отзывайте старый токен.
- `repository_dispatch` создаёт run сразу, но job может встать в очередь при
  нагрузке GitHub; `queue: max` защищает от отмены, не от задержки.
- Штатные cron-строки в `watchdog.yml` удалять нельзя — они второй канал.

## Ротация токенов (обязательно, по порядку)

1. **Cloudflare API Token**, которым выполнялся деплой, отозвать сразу:
   My Profile → API Tokens → Revoke. После деплоя он не нужен.
2. Сейчас в секрете `GH_PAT` лежит старый PAT из переписки. Создайте **новый
   fine-grained PAT** (`Actions: write`, только `github-open-source`) и замените
   секрет: панель Cloudflare → `ghos-v6-tick` → Settings → Variables and
   Secrets → `GH_PAT`, либо `printf '%s' "$NEW_PAT" | npx wrangler secret put GH_PAT`.
3. Только после замены отзовите оба старых PAT из переписки.

## Резервные варианты, если Cloudflare недоступен

**cron-job.org** (бесплатно, интервал от 1 мин, без кода):

```
POST https://api.github.com/repos/donjon1212322/github-open-source/dispatches
Authorization: Bearer <NEW_FINE_GRAINED_PAT>
Accept: application/vnd.github+json
Content-Type: application/json
X-GitHub-Api-Version: 2022-11-28

{"event_type":"watchdog_tick"}
```

Ограничения: таймаут 30 с, нет авто-ретраев, отключение после 25 подряд неудач.

**Свой ПК / Raspberry Pi:** обычный `cron` с тем же `curl` каждые 30 минут —
нужен постоянно включённый компьютер.

## Честно про гарантии

**100% гарантии нет.** У Cloudflare Cron Triggers тоже нет автоматических
повторов и алертов об ошибке; `503` у Gemini, недоступность Telegram/dev.to и
очереди GitHub никуда не делись. Система стала заметно надёжнее: пропущенный
тик подхватывается следующим через 30 минут, штатные cron GitHub остаются вторым
каналом, а watchdog догоняет пропущенные слоты по state и сообщает владельцу
только при реальной проблеме.

**Не менять:** `timezone`, UTC-сутки, лимит 3 поста/день и Make.com — это
отдельные решения владельца.
