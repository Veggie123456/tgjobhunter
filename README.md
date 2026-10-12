# TG Job Hunter

Telegram job alerts tuned for Daniil's operations-heavy background. It searches Philadelphia and remote roles across LinkedIn, ZipRecruiter, Indeed and Google Jobs through JobSpy, deduplicates them, scores fit, and sends only stronger matches.

## Search categories
Business Operations, Administrative Operations, Customer Operations, Scheduling/Workforce, Project Operations, Customer Support, Reservations/Travel, Ecommerce/Digital, QA/Tech Operations, and Web3/Crypto.

## Setup
1. In Telegram, message @BotFather and create a bot. Copy the token.
2. Send any message to the new bot.
3. In a browser open Telegram's getUpdates endpoint for your bot and copy your numeric chat id from the returned message.
4. In this GitHub repo go to Settings → Secrets and variables → Actions.
5. Add repository secrets named `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
6. Open Actions → Job Hunter Scan → Run workflow for the first test.

The workflow then scans hourly. It remembers previously alerted postings in `data/seen_jobs.json` so it does not repeatedly alert the same listing.

## Entry-level remote sales alerts
The hourly search also includes inbound sales, SDR/BDR, inside sales, account coordination, retention and sales support roles. Sales-category alerts are restricted to remote listings with **at least $20/hour (or $41,600/year) in explicitly advertised base pay**, excluding commission-only, OTE-only and unverified-pay postings. Commission and bonuses can be extra, but do not count toward the minimum.

International travel / work-from-anywhere language receives a ranking boost, but every alert reminds the candidate to verify work-from-abroad permission directly with the employer. This is a preference, not a verified guarantee. Non-sales search categories remain in place.

## Alert scoring
The score rewards operations, scheduling, onboarding, hiring, customer/client support, documentation, SOP, QA, ecommerce and Web3/crypto overlap. Remote and Philadelphia matches receive a boost. Commission-only, door-to-door, cold-calling and similar roles are penalized.

Edit `config.yaml` to change titles, thresholds or search locations.

## Notes
Job boards change markup and anti-bot behavior, so individual sources can occasionally fail while others continue. This bot does not bypass logins, CAPTCHAs, or platform access controls. V1 links you to the original application rather than auto-submitting applications.
