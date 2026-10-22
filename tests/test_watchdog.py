"""اختبارات حارس الصحة: متى يُنبّه، ومتى يسكت، وألا يكرر نفسه."""
from datetime import datetime, timedelta, timezone

import bot_watchdog as w

NY = w.NY


def run(job, end_ny, rc=0, mode="live"):
    end = end_ny.astimezone(timezone.utc)
    return {"start": end - timedelta(minutes=1), "end": end, "job": job, "mode": mode, "rc": rc}


MON_11 = datetime(2026, 10, 19, 11, 0, tzinfo=NY)     # الاثنين 11:00 نيويورك (جلسة عادية)


def test_in_session_only_during_trading_hours():
    assert w.in_session(MON_11)
    assert not w.in_session(datetime(2026, 10, 19, 9, 0, tzinfo=NY))        # قبل 9:50
    assert not w.in_session(datetime(2026, 10, 19, 16, 30, tzinfo=NY))      # بعد الإغلاق
    assert not w.in_session(datetime(2026, 10, 17, 11, 0, tzinfo=NY))       # سبت
    assert not w.in_session(datetime(2026, 11, 26, 11, 0, tzinfo=NY))       # عطلة الشكر
    assert not w.in_session(datetime(2026, 11, 27, 13, 30, tzinfo=NY))      # إغلاق مبكر 1:00


def healthy_runs(now):
    return [run("bot", now - timedelta(minutes=10)), run("stocks", now - timedelta(minutes=12)),
            run("commands", now - timedelta(minutes=5))]


def test_no_problems_when_everything_runs():
    assert w.find_problems(MON_11, healthy_runs(MON_11), {"stocks_watcher": "active"}, 40) == {}


def test_stale_job_is_reported():
    runs = [r for r in healthy_runs(MON_11) if r["job"] != "bot"] + [run("bot", MON_11 - timedelta(minutes=70))]
    p = w.find_problems(MON_11, runs, {"stocks_watcher": "active"}, 40)
    assert list(p) == ["stale:bot"]
    assert "70" in p["stale:bot"] or "7" in p["stale:bot"]


def test_job_never_ran_today_is_reported_after_the_allowed_gap():
    now = datetime(2026, 10, 19, 10, 50, tzinfo=NY)     # 60 دقيقة بعد بداية النافذة 9:50 ولا تشغيل
    p = w.find_problems(now, [], {"stocks_watcher": "active"}, 40)
    assert {"stale:bot", "stale:stocks", "stale:commands"} <= set(p)


def test_start_of_session_does_not_false_alarm():
    now = datetime(2026, 10, 19, 10, 5, tzinfo=NY)      # 15 دقيقة فقط بعد بداية النافذة
    assert w.find_problems(now, [], {"stocks_watcher": "active"}, 40) == {}


def test_two_failed_runs_in_a_row_are_reported():
    runs = healthy_runs(MON_11) + [run("stocks", MON_11 - timedelta(minutes=8), rc=1), run("stocks", MON_11 - timedelta(minutes=2), rc=1)]
    p = w.find_problems(MON_11, runs, {"stocks_watcher": "active"}, 40)
    assert "fail:stocks" in p


def test_single_failure_is_not_reported():
    runs = healthy_runs(MON_11) + [run("stocks", MON_11 - timedelta(minutes=2), rc=1)]
    assert "fail:stocks" not in w.find_problems(MON_11, runs, {"stocks_watcher": "active"}, 40)


def test_shadow_mode_runs_do_not_count():
    runs = [run("bot", MON_11 - timedelta(minutes=5), mode="shadow")]
    assert "stale:bot" in w.find_problems(MON_11 + timedelta(minutes=60), runs, {"stocks_watcher": "active"}, 40)


def test_long_job_down_and_disk_full():
    p = w.find_problems(MON_11, healthy_runs(MON_11), {"stocks_watcher": "inactive"}, 91)
    assert set(p) == {"down:stocks_watcher", "disk"}


def test_activating_counts_as_running():
    assert "down:stocks_watcher" not in w.find_problems(MON_11, healthy_runs(MON_11), {"stocks_watcher": "activating"}, 40)


def test_outside_session_only_disk_is_checked():
    sat = datetime(2026, 10, 17, 11, 0, tzinfo=NY)
    assert w.find_problems(sat, [], {}, 40) == {}
    assert set(w.find_problems(sat, [], {}, 95)) == {"disk"}


def test_alert_once_then_recovery_message():
    p = {"stale:bot": "⛔ bot متوقف"}
    msgs, st = w.update_state({}, p, MON_11)
    assert msgs == ["⛔ bot متوقف"]
    msgs, st = w.update_state(st, p, MON_11 + timedelta(minutes=5))       # المشكلة نفسها: لا تكرار
    assert msgs == []
    msgs, st = w.update_state(st, {}, MON_11 + timedelta(minutes=10))     # زالت
    assert len(msgs) == 1 and msgs[0].startswith("✅") and "bot" in msgs[0]
    assert st == {"pending": {}, "active": {}}


def test_service_down_needs_two_consecutive_checks():
    p = {"down:stocks_watcher": "⛔ down"}
    msgs, st = w.update_state({}, p, MON_11)
    assert msgs == []                                                      # الفحص الأول: ننتظر (قد يعيده المؤقت)
    msgs, st = w.update_state(st, p, MON_11 + timedelta(minutes=5))
    assert msgs == ["⛔ down"]
    msgs, st = w.update_state(st, {}, MON_11 + timedelta(minutes=10))
    assert msgs[0].startswith("✅")


def test_service_blip_that_recovers_never_alerts():
    msgs, st = w.update_state({}, {"down:stocks_watcher": "x"}, MON_11)
    msgs, st = w.update_state(st, {}, MON_11 + timedelta(minutes=5))
    assert msgs == [] and st["pending"] == {}


def test_parse_runs_skips_corrupt_lines():
    rows = [["2026-10-19T14:00:00Z", "2026-10-19T14:01:00Z", "bot", "live", "0"],
            ["garbage"], ["2026-10-19T14:00:00Z", "x", "bot", "live", "0"],
            ["2026-10-19T14:30:00Z", "2026-10-19T14:31:00Z", "bot", "live", "1"]]
    got = w.parse_runs(rows)
    assert [r["rc"] for r in got] == [0, 1]
