# Telegram-Subscription-Manager — Project Context

## Stack
- Python 3.14 (venv) — may need downgrade to 3.12 if asyncpg/SQLAlchemy 
  lack stable wheels for 3.14, decide during Epic 2
- aiogram v3 (Telegram bot framework)
- PostgreSQL (target), SQLAlchemy async + Alembic (target)
- Docker + docker-compose (target)
- Stripe (Checkout + Webhooks, BLIK payment method required)
- APScheduler (subscription expiration jobs)

## Current status
Epic 0 (repo foundation) — DONE. Secrets rotated, history cleaned via 
git filter-repo, .gitignore hardened, dependencies pinned.
Currently working: Epic 1 (typed config via pydantic-settings).

## STRICT GIT RULES — NON-NEGOTIABLE
1. One new, distinctly named branch per PR. Never commit to main directly.
2. Commit immediately after each individual file change — atomic commits, 
   scoped commit messages. Never batch unrelated changes.
3. Do NOT push until the entire assigned task is 100% complete and 
   self-verified.
If a task seems to require breaking these, stop and ask before proceeding.

## Structure
- main.py — entry point
- app/handlers.py — aiogram handlers/FSM
- app/keyboards.py — inline/reply keyboards
- .env — local secrets (untracked)
- .env.example — template, no real values

## Conventions
- No hardcoded secrets or IDs anywhere in source — always via config.
- Business logic must stay decoupled from aiogram handlers where possible.