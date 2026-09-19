# Email marketing

Lists, contacts, templates and campaigns, sent through the Mailcow relay by
the job runner, with open/click tracking, one-click unsubscribe, and IMAP
bounce/complaint handling. Everything is owner-scoped.

## Pieces

| Where | What |
|---|---|
| `deerflow/persistence/email_marketing/` | `em_*` tables + `EmailMarketingRepository` (migration `2026_09_19_email_marketing`) |
| `deerflow/email_marketing/templates.py` | Jinja2 **sandbox**, autoescape, strict undefined, no loaders; context = `contact.*`, `unsubscribe_url`, `view_in_browser_url`, `tracking_pixel`; click-link rewriting, pixel injection, text alternative |
| `deerflow/email_marketing/tracking.py` | HMAC tokens per send and purpose (`open`/`click`/`unsub`); click URLs carry their own HMAC → no open redirect |
| `deerflow/email_marketing/smtp.py` | one SMTP connection per batch, `RSET` between messages, reconnect once, 4xx → deferred, 5xx → hard/soft bounce, per-domain token bucket that reports `throttled` instead of sleeping; VERP envelope `bounce+<send>@<domain>`, `List-Unsubscribe` (+ `One-Click`), `Precedence: bulk`, `X-Nova-Send` |
| `deerflow/email_marketing/pipeline.py` | jobs `em.campaign.start` (snapshot recipients minus suppressions → `em_sends`) and `em.campaign.batch` (render per contact, send in a thread, record events, suppress hard bounces, requeue throttled/deferred, chain the next batch spaced by the throttle). Pause/cancel are read from the campaign row at the top of every batch |
| `deerflow/email_marketing/bounces.py`, `imap.py` | DSN (RFC 3464) + ARF (RFC 5965) parsing; UID-cursor IMAP poll (`em.bounce.poll`) that matches VERP / `X-Nova-Send`, suppresses, and handles `unsubscribe+*@` mailto replies |
| `deerflow/email_marketing/contacts_import.py` | CSV header guessing, normalisation, in-file dedupe, validation (`em.contacts.import` job) |
| `app/gateway/routers/email_marketing.py` | `/api/em/*` (session auth) |
| `app/gateway/routers/email_marketing_public.py` | `/api/em/t/o/{token}.gif`, `/api/em/t/c/{token}?u=&sig=`, `GET|POST /api/em/u/{token}` — public, token-authenticated, CSRF-exempt for the RFC 8058 POST |

Event types, campaign/contact statuses and suppression reasons are pinned by
`contracts/email_marketing_events_contract.json` on both sides.

## Switching it on

1. `.env`: `NOVA_EM_TRACKING_SECRET=$(openssl rand -hex 32)` and the bounce
   mailbox password (`NOVA_EM_BOUNCE_PASSWORD`).
2. Mailcow: a `bounce@<domain>` mailbox (Postfix's `recipient_delimiter=+`
   delivers `bounce+<send>@` and `reply+<send>@` there) and an
   `unsubscribe@<domain>` alias pointing at it. Settings › Email › "Ensure
   sender" does this through the Mailcow API once `MAILCOW_API_KEY` is set.
   DKIM/SPF/DMARC stay Mailcow's job; the same button reports DKIM status.
3. `config.yaml` → `email_marketing.enabled: true`, `public_base_url`,
   `tracking_secret: $NOVA_EM_TRACKING_SECRET`, `bounce_mailbox.password:
   $NOVA_EM_BOUNCE_PASSWORD`. Hot-reloads; the jobs container reads it per job.
4. A schedule for the poller: `POST /api/jobs/schedules` with
   `type: em.bounce.poll`, `cron: "*/5 * * * *"` (owner-scoped — the job
   polls that owner's mailbox), and optionally `em.events.prune` daily.

## Lifecycle

`draft` → (`send-now` | `schedule`) → `sending` ⇄ `paused` → `completed`,
or `cancelled`/`failed`. `GET /campaigns/{id}/preflight` says what would
stop a send: SMTP off, missing secret/base URL, a template that does not
compile, zero recipients after suppressions.

## Verifying

```bash
# from the API (session cookie): list + 2 own addresses + template + campaign
curl -s -X POST $NOVA/api/em/campaigns/$ID/send-now
# then watch /workspace/jobs (em.campaign.start → em.campaign.batch) and
# GET /api/em/campaigns/$ID/stats — sent, opened, clicked fill in as you open
# the mail; send one to a non-existent address on a real domain and the
# bounce poll marks it bounced_hard + suppressed within 5 minutes.
```

## Bridges

`deerflow/email_marketing/bridges.py`, built on each service's REST API and
reached through `nova-host-bridge`. URLs and API keys come from
`integrations.services.{mailcow,twenty,chatwoot}`; switches from
`email_marketing.bridges`.

| Bridge | Endpoint / job | What it does |
|---|---|---|
| Mailcow | `POST /api/em/bridges/mailcow/ensure-sender`, `GET …/mailcow/dkim/{domain}` | bounce mailbox + unsubscribe alias + DKIM status |
| Twenty | `POST …/twenty/sync-contacts` → job `em.twenty.sync`; `POST …/twenty/import` → job `em.twenty.import` | people ⇄ contacts by primary email |
| Chatwoot | inside `em.bounce.poll` | a human reply to `reply+<send>@` becomes a conversation in `bridges.chatwoot_inbox_id`, labelled `nova-campaign`, event `replied` |

`GET /api/em/bridges/status` says, per bridge, whether it is configured and why not.

