"""Firm-definable performance targets, versioned per tenant.

The star COMPUTATION is fixed and server-side — only the thresholds come from config.
Rows in performance_config are append-only: every change is a new version (that IS the
audit log: who, when, why). Scoring an item uses the version that was active when the
item completed, so changing a target never rewrites history; version 0 means "the
built-in defaults", which reproduce the original hardcoded scales exactly.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import PerformanceConfig

DAY_MS = 86400000

# Section defaults. hold_target_days drives the proposal/onboarding per-holder scale;
# grace_bands are the days-late edges for ★4/★3/★2 (on-time is always ★5, beyond → ★1 —
# lateness is vs the statutory/agreed due date, which is deliberately NOT configurable).
DEFAULTS = {
    "proposal": {"cycle_target_days": None, "hold_target_days": 0.5},
    "onboarding": {"cycle_target_days": None, "hold_target_days": 0.5},
    "duty": {"grace_bands": [1.0, 3.0, 7.0]},
    "invoicing": {"target_days": None, "grace_bands": [1.0, 3.0, 7.0]},
}

# Hold scale shape: multiples of hold_target_days → stars. With the default target (½ day)
# this reproduces the original scale exactly: ≤½d★5 · ≤1d★4½ · ≤2d★4 · ≤3d★3½ · ≤5d★3 · ≤7d★2.
HOLD_RATIOS = [(1, 5), (2, 4.5), (4, 4), (6, 3.5), (10, 3), (14, 2)]


# keys where a stored null is meaningful ("no target set"), not "fall back to default"
_NULLABLE_KEYS = {"cycle_target_days", "target_days"}


def merged(cfg: dict | None) -> dict:
    out = {}
    for section, defaults in DEFAULTS.items():
        got = (cfg or {}).get(section) or {}
        sec = {}
        for k, v in defaults.items():
            sec[k] = got.get(k, v) if k in _NULLABLE_KEYS else (got[k] if got.get(k) is not None else v)
        out[section] = sec
    return out


def hold_scale(hold_target_days: float) -> list[dict]:
    return [{"max_days": r * hold_target_days, "stars": s} for r, s in HOLD_RATIOS] + \
        [{"max_days": None, "stars": 1}]


def hold_stars(avg_days: float, hold_target_days: float) -> float:
    for step in hold_scale(hold_target_days):
        if step["max_days"] is None or avg_days <= step["max_days"]:
            return step["stars"]
    return 1


def band_stars(late_ms: int, grace_bands: list[float], capped: bool) -> int:
    """Duty/invoicing stars vs the due date. On/before due ★5; the three band edges are
    the firm's grace days for ★4/★3/★2; beyond ★1. `capped` (declared without proof /
    raised outside Baton) caps at ★3."""
    days_late = late_ms / DAY_MS
    x, y, z = grace_bands
    if late_ms <= 0:
        s = 5
    elif days_late <= x:
        s = 4
    elif days_late <= y:
        s = 3
    elif days_late <= z:
        s = 2
    else:
        s = 1
    return min(s, 3) if capped else s


def _d(x: float) -> str:
    return "½" if x == 0.5 else f"{x:g}"


def _days_label(x: float) -> str:
    return "½ day" if x == 0.5 else f"{x:g}d"


def _stars_label(s: float) -> str:
    return f"{int(s)}½" if s % 1 else f"{s:g}"


def hold_scale_text(hold_target_days: float) -> str:
    parts = [f"≤{_days_label(r * hold_target_days)} ★{_stars_label(s)}" for r, s in HOLD_RATIOS]
    return " · ".join(parts) + " · beyond ★1"


def duty_scale_text(grace_bands: list[float]) -> str:
    x, y, z = grace_bands
    return (f"completed on/before due ★5 · ≤{_d(x)}d late ★4 · ≤{_d(y)}d late ★3 · "
            f"≤{_d(z)}d late ★2 · beyond ★1 · declared without proof capped at ★3")


def invoicing_scale_text(inv_cfg: dict) -> str:
    x, y, z = inv_cfg["grace_bands"]
    anchor = (f"target: within {_d(inv_cfg['target_days'])}d of EL send"
              if inv_cfg.get("target_days") is not None else "vs the payment due date")
    return (f"invoice raised on/before due ★5 ({anchor}) · ≤{_d(x)}d late ★4 · ≤{_d(y)}d late ★3 · "
            f"≤{_d(z)}d late ★2 · beyond ★1 · declared raised outside Baton capped at ★3")


def scale_texts(cfg: dict) -> dict:
    return {
        "proposal_stars_scale_text": hold_scale_text(cfg["proposal"]["hold_target_days"]),
        "duty_stars_scale_text": duty_scale_text(cfg["duty"]["grace_bands"]),
        "onboarding_stars_scale_text": ("avg holding time per pass — "
                                        + hold_scale_text(cfg["onboarding"]["hold_target_days"])),
        "invoicing_stars_scale_text": invoicing_scale_text(cfg["invoicing"]),
    }


def validate(cfg: dict) -> list[str]:
    """Returns problems; empty list = valid. Applied to a fully-merged config."""
    errs = []
    for section in ("proposal", "onboarding"):
        t = cfg[section]["hold_target_days"]
        if not (isinstance(t, (int, float)) and 0 < t <= 90):
            errs.append(f"{section}.hold_target_days must be between 0 and 90 days")
        c = cfg[section]["cycle_target_days"]
        if c is not None and not (isinstance(c, (int, float)) and 0 < c <= 365):
            errs.append(f"{section}.cycle_target_days must be between 0 and 365 days, or empty")
    for section in ("duty", "invoicing"):
        bands = cfg[section]["grace_bands"]
        ok = (isinstance(bands, list) and len(bands) == 3
              and all(isinstance(b, (int, float)) and b > 0 for b in bands)
              and bands[0] < bands[1] < bands[2])
        if not ok:
            errs.append(f"{section}.grace_bands must be three increasing positive day counts")
    t = cfg["invoicing"]["target_days"]
    if t is not None and not (isinstance(t, (int, float)) and 0 < t <= 365):
        errs.append("invoicing.target_days must be between 0 and 365 days, or empty")
    return errs


class ConfigTimeline:
    """All config versions for a tenant, loaded once; .at(dt) resolves the version active
    at a completion timestamp (version 0 = built-in defaults, before any row existed)."""

    def __init__(self, db: Session, tenant_id):
        self.rows = db.scalars(
            select(PerformanceConfig).where(PerformanceConfig.tenant_id == tenant_id)
            .order_by(PerformanceConfig.version)
        ).all()

    def at(self, dt: datetime | None) -> tuple[dict, int]:
        dt = dt or datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        active = None
        for row in self.rows:
            created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
            if created <= dt:
                active = row
        if active is None:
            return merged(None), 0
        return merged(active.config), active.version

    def active(self) -> tuple[dict, int]:
        return self.at(None)
