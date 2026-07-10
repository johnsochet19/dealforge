"""Alert evaluation engine.

Given the latest observation + full history for a product, decide whether each
active alert fires. Pure rule functions keep this testable. Notification
delivery (email/SMS/webhook/etc.) is a separate concern -- here we record an
AlertEvent; a dispatcher would consume those. That separation is what lets you
add channels without touching rule logic.
"""
from datetime import datetime
from sqlalchemy import select
from ..models import Alert, AlertEvent, PriceObservation, Product
from .history import compute_stats
from .notify import dispatch


def _evaluate(rule_type, threshold, latest, stats) -> str | None:
    price = latest.price
    if rule_type == "price_below" and threshold is not None:
        if price < threshold:
            return f"Price ${price:.2f} is below your ${threshold:.2f} target."
    elif rule_type == "percent_off" and threshold is not None:
        baseline = stats.avg_90d or stats.average
        if baseline:
            pct = (baseline - price) / baseline * 100
            if pct >= threshold:
                return f"{pct:.0f}% off vs 90-day average (threshold {threshold:.0f}%)."
    elif rule_type == "lowest_ever":
        if price <= stats.lowest * 1.001:
            return f"New historical low: ${price:.2f}."
    elif rule_type == "back_in_stock":
        if latest.in_stock:
            return "Item is back in stock."
    elif rule_type == "coupon_appears":
        if latest.coupon and latest.coupon > 0:
            return f"Coupon available: ${latest.coupon:.2f} off."
    elif rule_type == "low_inventory":
        if latest.inventory_level is not None and threshold is not None:
            if latest.inventory_level <= threshold:
                return f"Low inventory: {latest.inventory_level} left."
    return None


def evaluate_alerts(db, deliver: bool = True) -> list[dict]:
    """Evaluate active alerts, record AlertEvents, and (by default) hand each
    fresh event to the notification dispatcher. Delivery is best-effort: a
    failing channel is recorded on the event, never raised. Pass deliver=False
    to record events without attempting delivery."""
    fired = []
    alerts = db.execute(select(Alert).where(Alert.active.is_(True))).scalars().all()
    for alert in alerts:
        obs = db.execute(
            select(PriceObservation)
            .where(PriceObservation.product_id == alert.product_id)
            .order_by(PriceObservation.observed_at)
        ).scalars().all()
        if not obs:
            continue
        stats = compute_stats(obs)
        latest = obs[-1]
        msg = _evaluate(alert.rule_type, alert.threshold, latest, stats)
        if msg:
            event = AlertEvent(alert_id=alert.id, message=msg,
                               triggered_at=datetime.utcnow())
            db.add(event)
            alert.last_triggered_at = datetime.utcnow()
            status = None
            if deliver:
                status = dispatch(db, alert, event, product=alert.product)
            fired.append({"alert_id": alert.id, "product_id": alert.product_id,
                          "message": msg, "delivery_status": status})
    db.commit()
    return fired
