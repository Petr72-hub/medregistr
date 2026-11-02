"""Independent acceptance examples for the agreed scheduling and stock rules.

Numbers below are deliberately literal expectations, not values recomputed with
the implementation's own dose or calendar helpers. Synthetic patients only.
"""
from datetime import date

import pytest
import sqlite3
from contextlib import closing

import medication as med
from storage import prepare_database


@pytest.fixture
def db(tmp_path, monkeypatch):
    import bot
    path = str(tmp_path / "medication.db")
    monkeypatch.setattr(bot, "DB_NAME", path)
    bot.init_db()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("INSERT INTO patients(id,last,first,weight) VALUES(1,'Тест','Пример',25)")
        connection.execute("INSERT INTO patients(id,last,first,weight) VALUES(2,'Второй','Пример',25)")
        connection.commit()
    med.initialize_medication(path)
    return path


def schedule(drug="infliximab", start="2027-01-01", weight=25, **kwargs):
    return med.preview_schedule(drug, start, weight, **kwargs)


def test_infliximab_induction_dates_and_unit_rounding():
    events = schedule(dose_choice=5)
    induction = [event for event in events if event["phase"] == "induction"]
    assert [event["date"] for event in induction] == ["2027-01-01", "2027-01-15", "2027-02-12"]
    assert [float(event["dose_mg"]) for event in induction] == [125, 125, 125]
    assert [event["units"] for event in induction] == [2, 2, 2]


def test_induction_weight_is_per_event_not_retroactive():
    events = schedule(dose_choice=5)
    updated = med.recalculate_event(events[1], 30)
    assert float(events[0]["dose_mg"]) == 125
    assert float(updated["dose_mg"]) == 150
    assert float(events[2]["dose_mg"]) == 125


def test_vedolizumab_exact_30kg_uses_lower_induction_band():
    events = schedule("vedolizumab", weight=30, dose_choice=5)
    induction = [event for event in events if event["phase"] == "induction"]
    assert [event["date"] for event in induction] == ["2027-01-01", "2027-01-15", "2027-02-12"]
    assert [float(event["dose_mg"]) for event in induction] == [150, 150, 150]


def test_adalimumab_exact_40kg_uses_upper_induction_band():
    events = schedule("adalimumab", weight=40, dose_choice=20)
    induction = [event for event in events if event["phase"] == "induction"]
    assert [event["date"] for event in induction] == ["2027-01-01", "2027-01-15"]
    assert [float(event["dose_mg"]) for event in induction] == [160, 80]


@pytest.mark.parametrize("weight", [20, 40, 65])
@pytest.mark.parametrize("fixed_dose", [20, 40])
def test_adalimumab_support_is_fixed_mg_not_mg_per_kg(weight, fixed_dose):
    events = schedule("adalimumab", weight=weight, phase="maintenance", dose_choice=fixed_dose,
                      interval_weeks=2, maintenance_start="2027-01-03")
    assert events
    assert all(float(event["dose_mg"]) == fixed_dose for event in events)


def test_adalimumab_uc_support_is_unavailable():
    with pytest.raises(ValueError):
        schedule("adalimumab", weight=40, phase="maintenance", dose_choice=40,
                 interval_weeks=2, diagnosis="UC")


def test_unapproved_7_5mg_per_kg_is_rejected():
    with pytest.raises(ValueError):
        schedule(dose_choice=7.5)


def test_support_respects_doctor_first_date_and_interval():
    events = schedule(phase="maintenance", dose_choice=10, interval_weeks=4,
                      maintenance_start="2027-02-03", through_date="2027-04-01")
    assert [event["date"] for event in events] == ["2027-02-03", "2027-03-03", "2027-03-31"]
    assert [float(event["dose_mg"]) for event in events] == [250, 250, 250]
    assert [event["units"] for event in events] == [3, 3, 3]


@pytest.mark.parametrize("start,anniversary", [("2027-01-31", "2028-01-31"), ("2024-02-29", "2025-02-28")])
def test_calendar_year_horizon_has_real_dates(start, anniversary):
    events = schedule(start=start, phase="maintenance", dose_choice=5, interval_weeks=4,
                      maintenance_start=start)
    dates = [date.fromisoformat(event["date"]) for event in events]
    assert dates[0] == date.fromisoformat(start)
    assert all(day <= date.fromisoformat(anniversary) for day in dates)
    assert (date.fromisoformat(anniversary) - dates[-1]).days < 28
    assert len(dates) == 14


def test_plan_is_reserve_and_issue_is_once_only(db):
    med.add_receipt(db, "infliximab", "2027-01-01", 10)
    event = med.manual_event("infliximab", "2027-01-05", 125)
    ids = med.save_plan(db, 1, [event])
    before = med.forecast(db, "2027-01-31", today="2027-01-01")["Инфликсимаб"]
    assert before["current_balance"] == 10
    assert before["reserve"] == 2
    assert med.issue_event(db, 1, ids[0], "2027-01-01", today="2027-01-01") is True
    assert med.issue_event(db, 1, ids[0], "2027-01-01", today="2027-01-01") is False
    after = med.forecast(db, "2027-01-31", today="2027-01-01")["Инфликсимаб"]
    assert after["current_balance"] == 8
    assert after["reserve"] == 0


def test_insufficient_issue_is_atomic(db):
    med.add_receipt(db, "infliximab", "2027-01-01", 1)
    ids = med.save_plan(db, 1, [med.manual_event("infliximab", "2027-01-05", 125)])
    with pytest.raises(ValueError):
        med.issue_event(db, 1, ids[0], "2027-01-01", today="2027-01-01")
    event = med.list_events(db, 1)[0]
    assert event["status"] == "planned"
    report = med.forecast(db, "2027-01-31", today="2027-01-01")["Инфликсимаб"]
    assert report["current_balance"] == 1
    assert report["reserve"] == 2


def test_future_receipt_does_not_conceal_earlier_shortage(db):
    med.add_receipt(db, "infliximab", "2027-01-20", 10)
    med.save_plan(db, 1, [med.manual_event("infliximab", "2027-01-05", 125)])
    report = med.forecast(db, "2027-01-31", today="2027-01-01")["Инфликсимаб"]
    assert report["current_balance"] == 0
    assert report["projected_balance"] == 8
    assert report["shortage"] == 2
    assert report["first_shortage_date"] == "2027-01-05"


def test_safety_stock_affects_purchase_without_physical_issue(db):
    med.add_receipt(db, "infliximab", "2027-01-01", 3)
    med.set_safety_stock(db, "infliximab", 2)
    med.save_plan(db, 1, [med.manual_event("infliximab", "2027-01-05", 125)])
    report = med.forecast(db, "2027-01-31", today="2027-01-01")["Инфликсимаб"]
    assert report["current_balance"] == 3
    assert report["reserve"] == 2
    assert report["projected_balance"] == 1
    assert report["shortage"] == 1
    assert report["first_shortage_date"] == "2027-01-05"


def test_actual_issue_cannot_be_future_dated(db):
    med.add_receipt(db, "infliximab", "2027-01-01", 10)
    ids = med.save_plan(db, 1, [med.manual_event("infliximab", "2027-01-05", 125)])
    with pytest.raises(ValueError):
        med.issue_event(db, 1, ids[0], "2027-01-02", today="2027-01-01")
    assert med.list_events(db, 1)[0]["status"] == "planned"


def test_wrong_patient_cannot_issue_or_cancel_event(db):
    med.add_receipt(db, "infliximab", "2027-01-01", 10)
    ids = med.save_plan(db, 1, [med.manual_event("infliximab", "2027-01-05", 125)])
    with pytest.raises(ValueError):
        med.issue_event(db, 2, ids[0], "2027-01-01", today="2027-01-01")
    with pytest.raises(ValueError):
        med.cancel_event(db, 2, ids[0])
    assert med.list_events(db, 1)[0]["status"] == "planned"


def test_cancel_releases_reserve_without_stock_movement(db):
    med.add_receipt(db, "infliximab", "2027-01-01", 10)
    ids = med.save_plan(db, 1, [med.manual_event("infliximab", "2027-01-05", 125)])
    med.cancel_event(db, 1, ids[0])
    report = med.forecast(db, "2027-01-31", today="2027-01-01")["Инфликсимаб"]
    assert report["current_balance"] == 10
    assert report["reserve"] == 0


def test_end_of_induction_plus_eight_weeks_is_real_date():
    events = schedule(start="2026-09-20", dose_choice=5, interval_weeks=8)
    induction = [event for event in events if event["phase"] == "induction"]
    maintenance = [event for event in events if event["phase"] != "induction"]
    assert induction[-1]["date"] == "2026-11-01"
    assert maintenance[0]["date"] == "2026-12-27"


@pytest.mark.parametrize("drug,interval", [("infliximab", 2), ("vedolizumab", 6), ("adalimumab", 4)])
def test_only_agreed_intervals_are_accepted(drug, interval):
    dose = 20 if drug == "adalimumab" else 5
    with pytest.raises(ValueError):
        schedule(drug, phase="maintenance", dose_choice=dose, interval_weeks=interval)


@pytest.mark.parametrize("reset,expected", [
    (False, ["2027-01-01", "2027-01-10", "2027-01-29", "2027-02-26"]),
    (True, ["2027-01-01", "2027-01-10", "2027-02-07", "2027-03-07"]),
])
def test_extra_event_option_preserves_or_resets_cadence(db, reset, expected):
    plan = schedule(phase="maintenance", dose_choice=5, interval_weeks=4,
                    maintenance_start="2027-01-01", through_date="2027-02-28")
    med.save_plan(db, 1, plan)
    extra = med.manual_event("infliximab", "2027-01-10", 125, extra=True)
    extra_id = med.save_plan(db, 1, [extra])[0]
    med.reschedule_event(db, 1, extra_id, "2027-01-10", reset_cadence=reset)
    assert [event["date"] for event in med.list_events(db, 1)] == expected


def test_new_weight_changes_future_plan_only_and_preserves_actual_issue(db):
    plan = schedule(phase="maintenance", dose_choice=5, interval_weeks=4,
                    maintenance_start="2027-01-01", through_date="2027-03-01")
    ids = med.save_plan(db, 1, plan)
    med.add_receipt(db, "infliximab", "2027-01-01", 20)
    med.issue_event(db, 1, ids[0], "2027-01-01", today="2027-01-01")
    med.update_pending_weight(db, 1, "2027-01-20", 50, today="2027-01-20")
    events = med.list_events(db, 1)
    assert [float(event["dose_mg"]) for event in events] == [125, 250, 250]
    assert [event["units"] for event in events] == [2, 3, 3]
    assert events[0]["status"] == "issued"
    report = med.forecast(db, "2027-03-01", today="2027-01-20")["Инфликсимаб"]
    assert report["current_balance"] == 18
    assert report["reserve"] == 6


def test_later_known_weight_is_not_overwritten_by_earlier_measurement(db):
    plan = schedule(phase="maintenance", dose_choice=5, interval_weeks=4,
                    maintenance_start="2027-01-01", through_date="2027-03-01")
    med.save_plan(db, 1, plan)
    med.update_pending_weight(db, 1, "2027-02-01", 50, today="2027-01-01")
    med.update_pending_weight(db, 1, "2027-01-15", 30, today="2027-01-01")
    assert [float(event["dose_mg"]) for event in med.list_events(db, 1)] == [125, 150, 250]


def test_legacy_rows_unchanged_and_future_entry_expenses_on_own_date(db):
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("INSERT INTO therapy(pid,date,drug,stage,vials_count,vial_mg,comment) VALUES(?,?,?,?,?,?,?)",
                           (1, "20.01.2027", "Инфликсимаб", "Поддерживающая терапия", 2, 100, "synthetic history"))
        connection.commit()
        before = connection.execute("SELECT * FROM therapy").fetchall()
        patients_before = connection.execute("SELECT * FROM patients").fetchall()
    med.add_receipt(db, "infliximab", "2027-01-01", 10)
    med.initialize_medication(db)
    early = med.forecast(db, "2027-01-19", today="2027-01-01")["Инфликсимаб"]
    later = med.forecast(db, "2027-01-31", today="2027-01-01")["Инфликсимаб"]
    assert early["current_balance"] == later["current_balance"] == 10
    assert early["projected_balance"] == 10
    assert later["projected_balance"] == 8
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute("SELECT * FROM therapy").fetchall() == before
        assert connection.execute("SELECT * FROM patients").fetchall() == patients_before
