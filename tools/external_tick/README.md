# Внешний будильник для watchdog (бесплатно)

**Зачем.** GitHub `schedule` не гарантирует доставку. 06.11.2026 не пришли
запуски в 07:27, 09:27, 11:27, 13:27 по Минску, а после 10:27 не было ни
одного запуска 3 часа 26 минут. Внутри GitHub это лечится только «большим
числом попыток»: в `watchdog.yml` добавлена вторая cron-строка `57 */2 * * *`.
Полностью убрать зависимость от планировщика GitHub помогает внешний будильник:
он дёргает **API** (`repository_dispatch`), а не общую очередь расписаний.

**Что уже сделано в репозитории (без внешних аккаунтов):**
- `watchdog.yml` принимает `repository_dispatch` с `types: [watchdog_tick]`;
- вторая cron-строка `57 */2 * * *` как резервный тик;
- `queue: max` в общей группе `state-writer` не даёт отменить созданный запуск;
- watchdog сам подтверждает слот по state, повторяет не чаще 30 минут и больше
  не шлёт тревожное сообщение на первый успешный dispatch.

**Что осталось сделать один раз вручную** (этот шаг нельзя выполнить из
репозитория: нужен аккаунт и новый токен).

## Вариант A — Cloudflare Worker (рекомендуемый)

По официальным лимитам Workers Free: **5 Cron Triggers на аккаунт**,
**100 000 запросов/сутки**, 10 мс CPU на вызов. Нам нужен **один** триггер
`*/30` = 48 вызовов/сутки.

1. Создайте бесплатный аккаунт Cloudflare и установите `wrangler`.
2. Скопируйте `wrangler.toml.example` → `wrangler.toml`, замените `OWNER/REPO`
   на `donjon1212322/github-open-source`.
3. Создайте **новый fine-grained PAT** только для этого репозитория с правом
   **Actions: Read and write**. Не используйте токен из переписки.
4. `npx wrangler secret put GH_PAT` — вставьте новый токен.
5. `npx wrangler deploy`.
6. Проверьте: через 30 минут в Actions появится run `Watchdog` с
   `event: repository_dispatch`.

## Вариант B — cron-job.org (без кода, проще)

Бесплатно, интервал от 1 минуты, кастомные заголовки и тело. Создайте задание
на каждые 30 минут:

```
POST https://api.github.com/repos/donjon1212322/github-open-source/dispatches
Authorization: Bearer <NEW_FINE_GRAINED_PAT>
Accept: application/vnd.github+json
Content-Type: application/json
X-GitHub-Api-Version: 2022-11-28

{"event_type":"watchdog_tick"}
```

Ограничения: таймаут 30 с, **нет авто-ретраев**, задание отключается после
25 подряд неудач. Поэтому основной штатный `schedule` удалять нельзя —
он остаётся вторым каналом.

## Вариант C — свой компьютер

Обычный `cron` на всегда включённом ПК / Raspberry Pi:

```
*/30 * * * * curl -sS -X POST \
  -H "Authorization: Bearer $GH_PAT" \
  -H "Accept: application/vnd.github+json" \
  -H "Content-Type: application/json" \
  -d '{"event_type":"watchdog_tick"}' \
  https://api.github.com/repos/donjon1212322/github-open-source/dispatches
```

## Важно и честно

- **Это не переезд проекта.** State-ветка, CI, Telegram, dev.to и Make.com
  остаются на GitHub. Внешний сервис только дёргает один API-эндпоинт.
- **Гарантии 100% нет ни у кого.** У Cloudflare cron тоже нет ретраев;
  `repository_dispatch` создаёт run сразу, но job всё равно может встать в
  очередь при нагрузке GitHub. `queue: max` защищает от отмены, не от задержки.
- **Не трогать** `timezone`, UTC-сутки, лимит 3 поста/день и Make.com —
  это отдельные решения владельца.
- **Токен**: новый, fine-grained, только этот репозиторий, `Actions: write`,
  с последующей ротацией. Старые PAT из переписки надо отозвать.
- Если внешний будильник не подключён, система работает как раньше: два cron-тика
  + `queue: max` + подтверждение по state, но при массовой недоставке GitHub
  пропуск всё ещё возможен. Тогда единственный сигнал — отсутствие `✅` и
 предупреждение от повторной неподтверждённой попытки.
