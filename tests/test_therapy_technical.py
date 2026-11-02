"""Technical therapy regressions, without asserting unapproved clinical rules."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

import bot


@pytest.fixture
def app(monkeypatch):
    instance = QApplication.instance() or QApplication([])
    monkeypatch.setattr(bot, "confirm", lambda *args: True)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    yield instance
    for widget in instance.topLevelWidgets():
        widget.hide()
        widget.deleteLater()
    instance.processEvents()


def test_diagnosis_change_recalculates_and_clears_previous_result(app):
    dialog = bot.AddTherapyDialog(1, 35)
    dialog.drug_box.setCurrentText("Адалимумаб")
    dialog.stage_box.setCurrentText("Поддерживающая терапия")
    assert dialog._calc_cache is None
    dialog.diag_box.setCurrentText("БК")
    assert dialog._calc_cache is not None
    dialog.diag_box.setCurrentText("ЯК")
    assert dialog._calc_cache is None
    dialog.save()
    assert dialog.result_data is None


@pytest.mark.parametrize("weight", [None, 0, -1, float("nan"), float("inf"), -float("inf"), "20"])
def test_invalid_weight_cannot_save_or_reuse_cached_dose(app, weight):
    dialog = bot.AddTherapyDialog(1, weight)
    assert dialog._calc_cache is None
    dialog.save()
    assert dialog.result_data is None
    dialog.weight_kg = 20
    dialog.recalc()
    assert dialog._calc_cache is not None
    dialog.weight_kg = weight
    dialog.recalc()
    assert dialog._calc_cache is None
    dialog.save()
    assert dialog.result_data is None


def test_legacy_root_attachment_fallback_and_database_local_priority(tmp_path, monkeypatch):
    legacy = tmp_path / "files" / "report.txt"
    legacy.parent.mkdir()
    legacy.write_text("legacy", encoding="utf-8")
    local = tmp_path / "dist" / "files" / "report.txt"
    local.parent.mkdir(parents=True)
    monkeypatch.setattr(bot, "DB_NAME", str(tmp_path / "dist" / "children.db"))
    assert bot.AnalysisWindow.file_path(None, "files/report.txt") == str(legacy)
    local.write_text("local", encoding="utf-8")
    assert os.path.normpath(bot.AnalysisWindow.file_path(None, "files/report.txt")) == str(local)


@pytest.mark.parametrize("date_text", ["31.02.2026", "29.02.2025", "01.01.26", "01.2026", ""])
def test_invalid_administration_date_cannot_save(app, date_text):
    dialog = bot.AddTherapyDialog(1, 20)
    dialog.date_edit.setText(date_text)
    dialog.save()
    assert dialog.result_data is None


def test_future_administration_date_is_allowed_and_normalized(app):
    dialog = bot.AddTherapyDialog(1, 20)
    dialog.date_edit.setText(" 1.2.2099 ")
    dialog.save()
    assert dialog.result_data["date"] == "01.02.2099"
