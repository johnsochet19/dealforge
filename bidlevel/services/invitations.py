"""Intelligent bid invitations: AI-ranked sub recommendations, generated
invitation emails, engagement tracking, and deadline-driven reminders."""
from __future__ import annotations

import secrets
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from bidlevel.models import BidPackage, Invitation, Project, Subcontractor
from bidlevel.services.subs import compliance_status

DEFAULT_REMINDER_HOURS = [168, 72, 24, 2]  # 7d, 3d, 24h, 2h before deadline

TRACKED_EVENTS = {"delivered", "opened", "clicked", "downloaded", "declined", "accepted", "submitted"}


def recommend_subs(db: Session, package_id: int, limit: int = 10) -> list[dict]:
    """Rank subcontractors for a package. Every score comes with its reasons."""
    pkg = db.get(BidPackage, package_id)
    if pkg is None:
        raise ValueError(f"package {package_id} not found")
    project = db.get(Project, pkg.project_id)
    out = []
    for sub in db.query(Subcontractor).all():
        score = 0.0
        reasons = []
        trade_match = pkg.trade in (sub.trades or [])
        div_match = pkg.csi_division in (sub.csi_divisions or [])
        if not (trade_match or div_match):
            continue
        if trade_match:
            score += 35
            reasons.append(f"Trade match: {pkg.trade}")
        elif div_match:
            score += 20
            reasons.append(f"CSI division match: {pkg.csi_division}")
        # capacity vs project size
        if project.estimated_value and sub.bonding_capacity:
            if sub.bonding_capacity >= project.estimated_value * 0.1:
                score += 10
                reasons.append("Bonding capacity adequate for package size")
            else:
                reasons.append("⚠ Bonding capacity may be tight for this project")
        # performance history
        for attr, weight, label in (
            ("quality_rating", 2.0, "quality"), ("schedule_rating", 2.0, "schedule"),
            ("communication_rating", 1.0, "communication"),
        ):
            v = getattr(sub, attr)
            if v is not None:
                score += v * weight
                reasons.append(f"{label.title()} rating {v:.1f}/5")
        if sub.safety_score is not None:
            score += sub.safety_score * 0.1
            reasons.append(f"Safety score {sub.safety_score:.0f}/100")
        if sub.emr is not None and sub.emr <= 1.0:
            score += 5
            reasons.append(f"EMR {sub.emr:.2f} (at/below industry average)")
        if sub.award_rate is not None:
            score += sub.award_rate * 10
            reasons.append(f"Historical award rate {sub.award_rate:.0%}")
        if sub.avg_bid_response_days is not None and sub.avg_bid_response_days <= 5:
            score += 5
            reasons.append(f"Responds in ~{sub.avg_bid_response_days:.0f} days")
        if sub.preferred_vendor:
            score += 8
            reasons.append("Preferred vendor")
        comp = compliance_status(sub)
        if comp["overall"] == "expired":
            score -= 25
            reasons.append("⚠ Expired compliance documents on file")
        elif comp["overall"] == "expiring_soon":
            score -= 5
            reasons.append("⚠ Compliance documents expiring soon")
        out.append({
            "sub_id": sub.id, "name": sub.name, "score": round(score, 1),
            "confidence": round(min(0.95, 0.5 + score / 200), 2),
            "reasons": reasons, "compliance": comp["overall"],
        })
    out.sort(key=lambda r: r["score"], reverse=True)
    return out[:limit]


def compose_email(project: Project, pkg: BidPackage, sub: Subcontractor, token: str) -> str:
    return (
        f"Subject: Invitation to Bid — {pkg.trade} — {project.name}\n\n"
        f"Dear {sub.name},\n\n"
        f"You are invited to submit a proposal for the {pkg.trade} package on "
        f"{project.name} (Project #{project.number}).\n\n"
        f"Bid due: {project.bid_due_date}\n"
        f"Documents: /portal/{token}/documents\n"
        f"Submit your bid: /portal/{token}\n"
        f"Calendar invite: attached (.ics)\n\n"
        f"Contact: {project.estimator or 'Estimating'} — {project.general_contractor}\n"
    )


def send_invitations(db: Session, package_id: int, sub_ids: list[int],
                     recommendations: dict[int, dict] | None = None) -> list[Invitation]:
    pkg = db.get(BidPackage, package_id)
    if pkg is None:
        raise ValueError(f"package {package_id} not found")
    project = db.get(Project, pkg.project_id)
    invitations = []
    already = {i.sub_id for i in pkg.invitations}
    for sid in sub_ids:
        if sid in already:
            continue
        sub = db.get(Subcontractor, sid)
        if sub is None:
            continue
        token = secrets.token_urlsafe(16)
        inv = Invitation(
            package_id=package_id,
            sub_id=sid,
            email_body=compose_email(project, pkg, sub, token),
            events=[{"event": "delivered", "at": datetime.now(timezone.utc).isoformat()}],
            recommendation=(recommendations or {}).get(sid, {}),
            reminder_rules=DEFAULT_REMINDER_HOURS,
            portal_token=token,
        )
        db.add(inv)
        invitations.append(inv)
    if pkg.status == "draft":
        pkg.status = "invited"
    db.commit()
    for i in invitations:
        db.refresh(i)
    return invitations


def record_event(db: Session, invitation_id: int, event: str) -> Invitation:
    if event not in TRACKED_EVENTS:
        raise ValueError(f"unknown event '{event}'")
    inv = db.get(Invitation, invitation_id)
    if inv is None:
        raise ValueError(f"invitation {invitation_id} not found")
    inv.events = list(inv.events) + [{"event": event, "at": datetime.now(timezone.utc).isoformat()}]
    if event in ("declined", "accepted", "submitted"):
        inv.status = event
    db.commit()
    db.refresh(inv)
    return inv


def run_reminders(db: Session, now: datetime | None = None) -> list[dict]:
    """Fire configured reminders for open invitations approaching the deadline."""
    now = now or datetime.now(timezone.utc)
    fired = []
    for inv in db.query(Invitation).filter(Invitation.status.in_(["sent", "accepted"])).all():
        project = db.get(Project, inv.package.project_id)
        if not project.bid_due_date:
            continue
        try:
            due = datetime.combine(date.fromisoformat(project.bid_due_date),
                                   datetime.min.time().replace(hour=17), tzinfo=timezone.utc)
        except ValueError:
            continue
        hours_left = (due - now).total_seconds() / 3600
        if hours_left < 0:
            continue
        for window in sorted(inv.reminder_rules):
            if hours_left <= window and window not in inv.reminders_sent:
                inv.reminders_sent = sorted(set(list(inv.reminders_sent) + [window]), reverse=True)
                fired.append({
                    "invitation_id": inv.id, "sub_id": inv.sub_id, "package_id": inv.package_id,
                    "window_hours": window, "hours_left": round(hours_left, 1),
                    "message": f"Reminder: bid due in {round(hours_left)} hours.",
                })
                break
    db.commit()
    return fired


def invitation_summary(inv: Invitation) -> dict:
    events = {e["event"] for e in inv.events}
    last = inv.events[-1]["at"] if inv.events else None
    resp_hours = None
    if len(inv.events) >= 2:
        t0 = datetime.fromisoformat(inv.events[0]["at"])
        t1 = datetime.fromisoformat(inv.events[-1]["at"])
        resp_hours = round((t1 - t0).total_seconds() / 3600, 2)
    return {
        "id": inv.id, "package_id": inv.package_id, "sub_id": inv.sub_id,
        "sub_name": inv.sub.name if inv.sub else "", "status": inv.status,
        "events": inv.events, "flags": {e: (e in events) for e in sorted(TRACKED_EVENTS)},
        "last_activity": last, "hours_to_respond": resp_hours,
        "recommendation": inv.recommendation, "portal_token": inv.portal_token,
        "reminders_sent": inv.reminders_sent, "email_body": inv.email_body,
    }
