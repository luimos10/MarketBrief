"""
Sesiones de mercado y nombres de archivo por edición.

Cada corrida del brief corresponde a la próxima apertura (Tokio, Londres o
Nueva York). Los outputs se nombran con la fecha de esa apertura y el slug de
la sesión para no sobrescribir las otras dos ediciones del día.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

EXCHANGES = [
    ("Tokio", "Asia/Tokyo", 9, 0, "tokio"),
    ("Londres", "Europe/London", 8, 0, "londres"),
    ("Nueva York", "America/New_York", 9, 30, "ny"),
]


def _as_utc(now_utc: datetime | None) -> datetime:
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def session_snapshot(now_utc: datetime | None = None) -> dict:
    """Devuelve la próxima apertura y los relojes locales de cada bolsa."""
    now = _as_utc(now_utc)
    clocks = []
    candidates = []

    for name, tz, open_hour, open_minute, slug in EXCHANGES:
        local = now.astimezone(ZoneInfo(tz))
        clocks.append((name, local.strftime("%H:%M")))

        candidate = local.replace(
            hour=open_hour, minute=open_minute, second=0, microsecond=0
        )
        if candidate <= local:
            candidate += timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)

        candidates.append((candidate - local, name, candidate, slug))

    _, name, opens_at, slug = min(candidates, key=lambda item: item[0])
    return {
        "name": name,
        "slug": slug,
        "opens_at": opens_at,
        "date": opens_at.date(),
        "clocks": clocks,
    }


def session_slug(now_utc: datetime | None = None) -> str:
    return session_snapshot(now_utc)["slug"]


def session_date(now_utc: datetime | None = None):
    return session_snapshot(now_utc)["date"]


def dated_name(prefix: str, ext: str, now_utc: datetime | None = None) -> str:
    """Ej: brief_2026-10-19_ny.html"""
    snap = session_snapshot(now_utc)
    return f"{prefix}_{snap['date']:%Y-%m-%d}_{snap['slug']}.{ext.lstrip('.')}"
