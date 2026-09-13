# Telegram Subscription Manager

A Telegram bot that manages paid access to a private channel. Subscribers pay
through Stripe Checkout (card or BLIK) or a manual admin-confirmed transfer,
get a single-use invite link on activation, and are warned and eventually
removed automatically once their subscription expires.

## How it works

One process runs three things concurrently:

- **Bot polling** (aiogram v3) — the subscriber-facing conversation: pricing,
  payment method choice, FSM for the manual-payment flow.
- **A small aiohttp webhook server** — receives Stripe's
  `checkout.session.completed` events, verifies their signature, and
  activates the subscription. Idempotent under concurrent/duplicate
  deliveries via a unique constraint on `(provider, provider_ref)`.
- **APScheduler**, backed by a Postgres jobstore (so schedules survive a
  container restart), running two daily jobs:
  - an expiration-warning job that DMs subscribers a few days before
    `expires_at` and moves them into an `EXPIRING` state;
  - an auto-kick job that removes anyone actually past `expires_at` from the
    channel — never a permanent ban (`ban` immediately followed by `unban`),
    and channel administrators/the creator are never removed.

Subscriptions move through an explicit state machine
(`PENDING -> ACTIVE -> EXPIRING -> EXPIRED -> KICKED`, with `KICKED -> ACTIVE`
on resubscribing) — see `app/domain/subscription.py`. Every transition goes
through it; nothing sets `status` directly.

In front of that process, a **Caddy** reverse proxy (its own container) is
the only thing exposed to the internet — it terminates HTTPS automatically
via Let's Encrypt and forwards everything to the bot's webhook port over the
Docker network. See [HTTPS in production](#https-in-production-caddy) below.

## Tech stack

- Python, [aiogram v3](https://docs.aiogram.dev/)
- PostgreSQL, SQLAlchemy (async) + Alembic migrations
- [Stripe](https://stripe.com/) Checkout + Webhooks (BLIK enabled)
- [APScheduler](https://apscheduler.readthedocs.io/), `AsyncIOScheduler` with a
  `SQLAlchemyJobStore`
- Docker + docker-compose

## Running locally

1. Copy `.env.example` to `.env` and fill in real values (see below).
2. Start everything:

   ```
   docker compose up --build
   ```

   This brings up Postgres and the bot/webhook/scheduler container. Migrations
   are not run automatically — apply them once the db is up:

   ```
   docker compose exec bot python -m alembic upgrade head
   ```

3. Stripe webhooks need a public URL during local development — point
   `stripe listen --forward-to localhost:<WEBHOOK_PORT>/webhook/stripe` at the
   container (or use `stripe trigger` for a one-off test event), and put the
   signing secret it prints into `STRIPE_WEBHOOK_SECRET`.

   Note: since Caddy became the only public entry point, the bot's webhook
   port is no longer published to the host, so `localhost:<WEBHOOK_PORT>`
   above won't resolve as-is. Either add a temporary
   `docker-compose.override.yml` republishing that port for local testing,
   or run `python main.py` directly on the host against the dockerized `db`
   (with `DB_HOST=localhost`) instead of through docker-compose.

## HTTPS in production (Caddy)

The real server is reached at a free [sslip.io](https://sslip.io) hostname
that resolves straight to its public IP — no domain purchase or DNS setup
needed. sslip.io accepts either the dotted (`A.B.C.D.sslip.io`) or
hyphenated (`A-B-C-D.sslip.io`) form of the IP; both resolve to the same
address, so if the server's IP ever changes, update the `Caddyfile`'s
hostname to match the new IP in whichever form is convenient.

Caddy (`caddy:2-alpine`) is the only container publishing ports 80/443; it
terminates HTTPS via automatic Let's Encrypt issuance and reverse-proxies
everything to `bot:8000` over the Docker network. Certificate/account state
lives in a named volume (`caddy_data`) so a restart doesn't re-issue.
Real issuance can only be confirmed once deployed to the actual public
server — ports 80/443 need to be reachable from the internet for Let's
Encrypt's HTTP-01 challenge to succeed, which no local environment provides.

## Environment variables

All are read via `app/config.py` (typed, `pydantic-settings`); see
`.env.example` for the authoritative, up-to-date list. In short:

| Variable | Purpose |
|---|---|
| `BOT_TOKEN` | Telegram bot token from @BotFather |
| `BOT_USERNAME` | Bot's `@username`, no leading `@` — used to build deep links |
| `ADMIN_ID` | Telegram user id that receives admin notifications and can confirm manual payments |
| `DATABASE_URL` | Optional full override; otherwise built from the `DB_*` vars below |
| `DB_USER`, `DB_PASSWORD`, `DB_NAME` | Postgres credentials (docker-compose db service) |
| `DB_HOST`, `DB_PORT`, `DB_HOST_PORT` | Connection overrides — defaults match the docker-compose network |
| `STRIPE_SECRET_KEY` | Stripe API secret key |
| `STRIPE_WEBHOOK_SECRET` | Signs incoming Stripe webhooks; requests are refused while empty |
| `CHANNEL_ID` | The private channel subscribers get invited to; invite/removal steps are skipped with a warning while unset |
| `WEBHOOK_PORT` | Port the Stripe webhook server listens on inside the container. Not published to the host — Caddy is the only public entry point (see below) |
| `WEBHOOK_HOST` | Interface the webhook server binds to inside the container; leave as `0.0.0.0` so Caddy can reach it over the Docker network |
| `DRY_RUN_KICK` | When `true`, the auto-kick job logs what it would do instead of actually removing anyone or sending DMs — meant for observing the first production run safely |

## Development notes

This project's development has been AI-assisted (Claude Code), with commits
reviewed and self-verified against the running stack before merge — see
commit messages and PR history for the reasoning behind non-obvious changes.
