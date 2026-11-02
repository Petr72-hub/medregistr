"""Offscreen UI workflows against an isolated database; no clinical assertions."""
import os
import sqlite3
from contextlib import closing

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

import bot
import medication as med
import medication_ui as ui


@pytest.fixture(scope="session")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def sandbox(tmp_path, monkeypatch, app):
    path = str(tmp_path / "ui.db")
    monkeypatch.setattr(bot, "DB_NAME", path)
    bot.init_db()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("INSERT INTO patients(id,last,first,birth,weight) VALUES(1,'Тест','Пример','01.01.2015',25)")
        connection.commit()
    med.initialize_medication(path)
    errors = []
    monkeypatch.setattr(ui, "confirm", lambda *args: True)
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *args: errors.append(args[1:]))
    yield path, errors
    for widget in app.topLevelWidgets():
        widget.hide()
        widget.deleteLater()
    app.processEvents()


def configured(path):
    planner = ui.TherapyPlanner(path, 1)
    planner.start_date.setDate(QDate(2026, 9, 20))
    planner.end_date.setDate(QDate(2027, 9, 20))
    planner.weight_spin.setValue(25)
    return planner


def test_preview_edit_induction_weight_save_and_reopen(sandbox):
    path, errors = sandbox
    planner = configured(path)
    planner.preview_plan()
    assert planner.preview_table.rowCount() > 3
    assert float(planner.preview_table.item(0, 3).text()) == 125
    assert planner.preview_table.item(0, 5).text() == "2"
    planner.preview_table.item(1, 2).setText("30")
    assert float(planner.preview_table.item(1, 3).text()) == 150
    assert float(planner.preview_table.item(0, 3).text()) == 125
    planner.save_plan()
    reopened = ui.TherapyPlanner(path, 1)
    assert reopened.events_table.rowCount() == planner.preview_table.rowCount()
    assert float(reopened.events_table.item(1, 3).text()) == 150
    assert all(event["status"] == "planned" for event in med.list_events(path, 1))
    assert not errors


def test_maintenance_first_date_suggestion_and_override(sandbox):
    path, _ = sandbox
    planner = configured(path)
    assert planner.maintenance_date.date() == QDate(2026, 12, 27)
    planner.custom_maintenance.setChecked(True)
    planner.maintenance_date.setDate(QDate(2026, 12, 28))
    planner.preview_plan()
    support = [event for event in planner.preview_events if event["phase"] == "maintenance"]
    assert support[0]["date"] == "2026-12-28"


def test_adalimumab_support_options_are_fixed_mg_and_uc_rejected(sandbox):
    path, errors = sandbox
    planner = configured(path)
    planner.drug_box.setCurrentText("Адалимумаб")
    assert [planner.dose_box.itemText(i) for i in range(planner.dose_box.count())] == ["20 мг", "40 мг"]
    planner.phase_box.setCurrentIndex(planner.phase_box.findData("maintenance"))
    planner.weight_spin.setValue(50)
    planner.preview_plan()
    assert all(float(event["dose_mg"]) == 20 for event in planner.preview_events)
    planner.diagnosis_box.setCurrentIndex(planner.diagnosis_box.findData("UC"))
    planner.preview_plan()
    assert errors
    assert not planner.save_button.isEnabled()


def test_stale_preview_cannot_be_saved_after_input_change(sandbox):
    path, _ = sandbox
    planner = configured(path)
    planner.preview_plan()
    assert planner.save_button.isEnabled()
    planner.weight_spin.setValue(50)
    assert not planner.save_button.isEnabled()
    planner.save_plan()
    assert med.list_events(path, 1) == []


def test_declined_save_and_cancel_do_not_write(sandbox, monkeypatch):
    path, _ = sandbox
    planner = configured(path)
    planner.preview_plan()
    monkeypatch.setattr(ui, "confirm", lambda *args: False)
    planner.save_plan()
    assert med.list_events(path, 1) == []
    planner.show()
    planner.reject()
    assert planner.isVisible()


def test_actual_issue_ui_once_only(sandbox, monkeypatch):
    path, errors = sandbox
    today = QDate.currentDate().toString("yyyy-MM-dd")
    med.add_receipt(path, "infliximab", today, 10)
    med.save_plan(path, 1, [med.manual_event("infliximab", today, 125)])
    planner = ui.TherapyPlanner(path, 1)
    planner.events_table.selectRow(0)
    monkeypatch.setattr(ui.DateActionDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    planner.issue_selected()
    assert not errors
    planner.events_table.selectRow(0)
    planner.issue_selected()
    report = med.forecast(path, today)["Инфликсимаб"]
    assert report["current_balance"] == 8
    assert report["reserve"] == 0
    assert med.list_events(path, 1)[0]["status"] == "issued"


def test_readonly_controls_and_handlers_preserve_plan(sandbox):
    path, _ = sandbox
    today = QDate.currentDate().toString("yyyy-MM-dd")
    med.save_plan(path, 1, [med.manual_event("infliximab", today, 125)])
    before = med.list_events(path, 1)
    planner = ui.TherapyPlanner(path, 1, readonly=True)
    planner.events_table.selectRow(0)
    assert not planner.tabs.isTabEnabled(0)
    assert not planner.tabs.isTabEnabled(2)
    for button in (planner.issue_button, planner.move_button, planner.cancel_button, planner.extra_button):
        assert not button.isEnabled()
    for handler in (planner.save_plan, planner.issue_selected, planner.cancel_selected,
                    planner.reschedule_selected, planner.add_extra, planner.update_weight):
        handler()
    assert med.list_events(path, 1) == before
    assert planner.events_table.item(0, 0).data(Qt.ItemDataRole.UserRole) == before[0]["id"]


def test_pharmacy_receipt_and_separate_safety_stock(sandbox):
    path, errors = sandbox
    pharmacy = ui.PharmacyWindow(path)
    pharmacy.quantity_spin.setValue(10)
    pharmacy.add_receipt()
    pharmacy.safety_spin.setValue(3)
    pharmacy.save_safety()
    report = med.forecast(path, QDate.currentDate().toString("yyyy-MM-dd"))["Инфликсимаб"]
    assert report["current_balance"] == 10
    assert report["reserve"] == 0
    assert report["safety_stock"] == 3
    assert pharmacy.receipts_table.rowCount() == 1
    assert not errors
