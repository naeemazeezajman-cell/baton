"""Daily reminder sweep — 07:00 Asia/Dubai (STRUCTURE.md: scheduler.py).

For each user with overdue duties: one summary email + a notices row.
For each accountant with unraised or overdue receivables: one receivables digest.
Idempotent per day — a digest_runs row records the last run (unique on run_date).
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import emails
from .db import SessionLocal
from .models import Client, DigestRun, Duty, Notice, Onboarding, OnboardingItem, Payment, Tenant, User
from .workflow import now

ONBOARDING_AGING_DAYS = 3  # baton held this long without movement joins the daily digest

log = logging.getLogger("baton.scheduler")

DUBAI = ZoneInfo("Asia/Dubai")


def _received(p: Payment) -> float:
    return sum(float(r.get("amount", 0)) for r in p.receipts)


def run_daily_digest(db: Session, force: bool = False) -> dict:
    """One sweep across all tenants. Returns a summary; no-op if already run today."""
    today = datetime.now(DUBAI).date()
    if not force and db.scalar(select(DigestRun).where(DigestRun.run_date == today)):
        return {"ran": False, "reason": f"digest already ran for {today}"}
    db.add(DigestRun(run_date=today))
    try:
        db.flush()
    except IntegrityError:  # concurrent run won the race
        db.rollback()
        return {"ran": False, "reason": f"digest already ran for {today}"}

    t = now()
    sent_duty, sent_recv = 0, 0
    for tenant in db.scalars(select(Tenant)).all():
        users = {u.id: u for u in db.scalars(select(User).where(User.tenant_id == tenant.id, User.active.is_(True)))}

        # per-user overdue duties + aging onboarding requests (whoever holds the baton)
        overdue = db.scalars(select(Duty).where(
            Duty.tenant_id == tenant.id, Duty.closed.is_(False), Duty.next_due < t
        ).order_by(Duty.next_due)).all()
        by_staff: dict = {}
        for d in overdue:
            by_staff.setdefault(d.staff_id, []).append(d)

        aging = db.scalars(select(Onboarding).where(
            Onboarding.tenant_id == tenant.id, Onboarding.status == "in_progress",
            Onboarding.holder.is_not(None),
            Onboarding.holder_since < t - timedelta(days=ONBOARDING_AGING_DAYS),
        ).order_by(Onboarding.holder_since)).all()
        by_holder: dict = {}
        for ob in aging:
            by_holder.setdefault(ob.holder, []).append(ob)
        clients_by_id = {c.id: c for c in db.scalars(select(Client).where(Client.tenant_id == tenant.id))}
        open_counts = dict(db.execute(
            select(OnboardingItem.onboarding_id, func.count()).where(
                OnboardingItem.tenant_id == tenant.id, OnboardingItem.status == "requested"
            ).group_by(OnboardingItem.onboarding_id)
        ).all()) if aging else {}

        for uid_ in set(by_staff) | set(by_holder):
            u = users.get(uid_)
            if not u:
                continue
            duties = by_staff.get(uid_, [])
            obs = by_holder.get(uid_, [])
            sections, notice_bits = [], []
            if duties:
                lines = [
                    f"- {d.client_name} — {d.service}: due {d.next_due:%d %b %Y} "
                    f"({max(1, round((t - d.next_due).total_seconds() / 86400))}d overdue)"
                    for d in duties
                ]
                sections.append(f"You have {len(duties)} overdue dut{'y' if len(duties) == 1 else 'ies'}:\n\n"
                                + "\n".join(lines) +
                                "\n\nComplete each with proof of work (deliverables / filed returns), or declare "
                                "completion with a reason.")
                notice_bits.append(f"{len(duties)} overdue dut{'y' if len(duties) == 1 else 'ies'}")
            if obs:
                lines = [
                    f"- {clients_by_id.get(ob.client_id).name if clients_by_id.get(ob.client_id) else '—'} — "
                    f"{ob.service}: baton with you for "
                    f"{max(1, round((t - ob.holder_since).total_seconds() / 86400))}d"
                    f"{f' ({open_counts[ob.id]} open item(s))' if open_counts.get(ob.id) else ''}"
                    for ob in obs
                ]
                sections.append(f"{len(obs)} onboarding request(s) are aging with you:\n\n" + "\n".join(lines) +
                                "\n\nPass the baton — provide or resolve the open items, or send your requests.")
                notice_bits.append(f"{len(obs)} aging onboarding request(s)")
            body = (f"Good morning {u.name},\n\n" + "\n\n".join(sections) +
                    "\n\nReminders repeat daily until each item moves.\n\n— Baton")
            subject_bits = ([f"{len(duties)} overdue deadline(s)"] if duties else []) + \
                           ([f"{len(obs)} aging onboarding(s)"] if obs else [])
            emails._send(u.email, "Baton — " + ", ".join(subject_bits), body,
                         db=db, tenant_id=tenant.id)
            db.add(Notice(tenant_id=tenant.id, user_id=u.id,
                          text_=f"Daily digest: {' and '.join(notice_bits)} need your action"))
            sent_duty += 1

        # per-accountant receivables
        pays = db.scalars(select(Payment).where(Payment.tenant_id == tenant.id)).all()
        attention = [p for p in pays
                     if _received(p) < float(p.amount) - 0.5
                     and (not p.invoice_raised or p.due_at < t)]
        accountants = [u for u in users.values() if u.role == "Accountant"]
        if attention and accountants:
            lines = []
            for p in attention:
                state = "invoice NOT raised" if not p.invoice_raised else "invoice raised, unpaid"
                od = (t - p.due_at).total_seconds() / 86400
                od_txt = f", {max(1, round(od))}d overdue" if od > 0 else ""
                lines.append(f"- {p.label}: AED {float(p.amount) - _received(p):,.0f} outstanding ({state}{od_txt})")
            for acct in accountants:
                body = (f"Good morning {acct.name},\n\n"
                        f"{len(attention)} receivable(s) need attention:\n\n" + "\n".join(lines) +
                        "\n\nMark invoices raised and record receipts as they arrive. "
                        "Reminders repeat daily until each receipt status is updated.\n\n— Baton")
                emails._send(acct.email, f"Baton — receivables digest ({len(attention)} item(s))", body,
                             db=db, tenant_id=tenant.id)
                db.add(Notice(tenant_id=tenant.id, user_id=acct.id,
                              text_=f"Daily digest: {len(attention)} receivable(s) awaiting invoice/receipt updates"))
                sent_recv += 1

    db.commit()
    return {"ran": True, "date": str(today), "duty_digests": sent_duty, "receivables_digests": sent_recv}


def _job():
    db = SessionLocal()
    try:
        result = run_daily_digest(db)
        log.info("daily digest: %s", result)
    finally:
        db.close()


def start_scheduler():
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BackgroundScheduler(timezone=DUBAI)
    scheduler.add_job(_job, CronTrigger(hour=7, minute=0, timezone=DUBAI), id="daily-digest",
                      misfire_grace_time=3600)
    scheduler.start()
    log.info("scheduler started — daily digest at 07:00 Asia/Dubai")
    return scheduler
