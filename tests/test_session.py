from datetime import datetime, timezone

from modules.session import dated_name, session_snapshot


def test_pre_apertura_ny():
    snap = session_snapshot(datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc))
    assert snap["slug"] == "ny"
    assert snap["name"] == "Nueva York"
    assert f"{snap['date']:%Y-%m-%d}" == "2026-10-19"


def test_pre_apertura_londres():
    snap = session_snapshot(datetime(2026, 10, 23, 6, 30, tzinfo=timezone.utc))
    assert snap["slug"] == "londres"
    assert f"{snap['date']:%Y-%m-%d}" == "2026-10-23"


def test_pre_apertura_tokio():
    snap = session_snapshot(datetime(2026, 10, 22, 23, 0, tzinfo=timezone.utc))
    assert snap["slug"] == "tokio"
    assert f"{snap['date']:%Y-%m-%d}" == "2026-10-23"


def test_fin_de_semana_salta_a_lunes():
    snap = session_snapshot(datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc))
    assert snap["slug"] == "tokio"
    assert f"{snap['date']:%Y-%m-%d}" == "2026-10-26"


def test_dated_name():
    name = dated_name("brief", "html",
                      datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc))
    assert name == "brief_2026-10-19_ny.html"
