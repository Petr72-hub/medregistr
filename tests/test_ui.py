"""Behavioural UI regression checks; all patient data is synthetic and temporary."""
import json
import os
import sqlite3
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

import bot


@pytest.fixture(scope="session")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def sandbox(tmp_path, monkeypatch, app):
    monkeypatch.setattr(bot, "DB_NAME", str(tmp_path / "test.db"))
    monkeypatch.setattr(bot, "FILES_DIR", str(tmp_path / "files"))
    monkeypatch.setattr(bot, "confirm", lambda *args: True)
    messages = []
    for method in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, method, lambda *args: messages.append(args[1:]))
    bot.init_db()
    yield tmp_path, messages
    for widget in app.topLevelWidgets():
        widget.hide()
        widget.deleteLater()
    app.processEvents()


def rows(sql, params=()):
    with sqlite3.connect(bot.DB_NAME) as connection:
        return connection.execute(sql, params).fetchall()


def patient(last="Тестовый", first="Пациент"):
    form = bot.PatientForm()
    form.last.setText(last)
    form.first.setText(first)
    form.middle.setText("Примерович")
    form.birth.setText("15.06.2015")
    form.addr_reg.setText("Учебный адрес 1")
    form.btn_copy.click()
    form.uch.setText("7")
    form.doctor.setText("Тестовый Педиатр")
    form.phone.setText("89991234567")
    form.invalid.setChecked(True)
    form.save()
    assert form.result() == QDialog.DialogCode.Accepted
    return form.saved_id


def get_patient(pid):
    return rows(f"SELECT {bot.PATIENT_COLUMNS} FROM patients WHERE id=?", (pid,))[0]


def test_passport_roundtrip_and_edit(sandbox):
    pid = patient()
    original = get_patient(pid)
    assert original[1:5] == ("Тестовый", "Пациент", "Примерович", "15.06.2015")
    assert original[7] == original[8] == "Учебный адрес 1"
    assert original[12] == "+7(999)-123-45-67"
    assert original[13] == 1
    form = bot.PatientForm(original)
    form.addr_fact.setText("Учебный адрес 2")
    form.save()
    assert get_patient(pid)[8] == "Учебный адрес 2"
    assert rows("SELECT count(*) FROM patients") == [(1,)]


@pytest.mark.parametrize("kind", ["patient", "analysis"])
@pytest.mark.parametrize("action", ["cancel", "close", "escape"])
def test_declined_cancel_keeps_window_and_data(sandbox, monkeypatch, app, kind, action):
    pid = patient()
    form = bot.PatientForm(get_patient(pid)) if kind == "patient" else bot.AnalysisWindow(pid)
    form.show()
    app.processEvents()
    asked = []
    monkeypatch.setattr(bot, "confirm", lambda *args: asked.append(args) or False)
    if action == "escape":
        QTest.keyClick(form, Qt.Key.Key_Escape)
    elif action == "close":
        form.close()
    else:
        form.cancel()
    app.processEvents()
    assert asked
    assert form.isVisible()
    assert rows("SELECT count(*) FROM analyses") == [(0,)]
    monkeypatch.setattr(bot, "confirm", lambda *args: True)
    form.close()
    assert not form.isVisible()


def test_declined_save_writes_nothing(sandbox, monkeypatch):
    monkeypatch.setattr(bot, "confirm", lambda *args: False)
    form = bot.PatientForm()
    form.last.setText("Тест")
    form.first.setText("Пример")
    form.birth.setText("01.01.2015")
    form.save()
    assert rows("SELECT count(*) FROM patients") == [(0,)]


def test_readonly_passport_and_analyses(sandbox):
    pid = patient()
    passport = bot.PatientForm(get_patient(pid), readonly=True)
    for name in ("last", "first", "middle", "birth", "gender", "mkb", "addr_reg",
                 "addr_fact", "btn_copy", "clinic", "uch", "doctor", "phone", "invalid"):
        assert not getattr(passport, name).isEnabled()
    editable = bot.AnalysisWindow(pid)
    editable.add_block("ОАК", {"Гемоглобин": "123"})
    editable.save()
    view = bot.AnalysisWindow(pid, readonly=True)
    for row in range(view.t.rowCount()):
        assert not view.t.item(row, 2).flags() & Qt.ItemFlag.ItemIsEditable


def test_all_analysis_types_save_reopen_and_edit(sandbox):
    pid = patient()
    form = bot.AnalysisWindow(pid)
    expected = {}
    for index, (kind, definitions) in enumerate(bot.ANALYSIS_DEFS.items()):
        values = {label: f"результат {index}-{n}" for n, (label, _) in enumerate(definitions)}
        expected[kind] = values
        form.add_block(kind, values)
    form.save()
    persisted = rows("SELECT type,data FROM analyses WHERE pid=? ORDER BY id", (pid,))
    assert len(persisted) == 3
    assert {kind: json.loads(data) for kind, data in persisted} == expected
    reopened = bot.AnalysisWindow(pid)
    for block in reopened.blocks:
        for offset, (label, unit) in enumerate(bot.ANALYSIS_DEFS[block["type"]]):
            row = block["start"] + offset
            assert reopened.t.item(row, 2).text() == expected[block["type"]][label]
            assert reopened.t.item(row, 3).text() == unit
    reopened.t.item(0, 2).setText("исправлено")
    reopened.save()
    assert rows("SELECT count(*) FROM analyses") == [(3,)]
    assert json.loads(rows("SELECT data FROM analyses ORDER BY id")[0][0])["Гемоглобин"] == "исправлено"


def test_main_has_no_id_and_filtered_selection_uses_patient_id(sandbox):
    patient("Первый", "Пациент")
    wanted = patient("Второй", "Пациент")
    window = bot.App()
    assert window.t.columnCount() == 4
    assert [window.t.horizontalHeaderItem(i).text() for i in range(4)] == ["ФИО", "Дата рождения", "МКБ-10", "Пол"]
    window.search_box.setText("Второй")
    assert window.t.rowCount() == 1
    window.t.selectRow(0)
    assert window.get()[0] == wanted


def test_attachment_cancel_has_no_persistent_effect(sandbox, monkeypatch):
    directory, _ = sandbox
    source = directory / "result.txt"
    source.write_text("synthetic report", encoding="utf-8")
    pid = patient()
    monkeypatch.setattr(bot.QFileDialog, "getOpenFileName", lambda *args: (str(source), ""))
    form = bot.AnalysisWindow(pid)
    form.add_block("ОАК")
    form.handle_file(0)
    form.cancel()
    assert rows("SELECT count(*) FROM analyses") == [(0,)]
    assert not list(Path(bot.FILES_DIR).glob("**/*"))


def test_identical_attachment_names_do_not_overwrite(sandbox, monkeypatch):
    directory, _ = sandbox
    source = directory / "same.txt"
    pid = patient()
    monkeypatch.setattr(bot.QFileDialog, "getOpenFileName", lambda *args: (str(source), ""))
    for content in ("first report", "second report"):
        source.write_text(content, encoding="utf-8")
        form = bot.AnalysisWindow(pid)
        form.add_block("ОАК")
        form.handle_file(len(form.blocks) - 1)
        form.save()
    filenames = [row[0] for row in rows("SELECT file FROM analyses ORDER BY id")]
    assert len(set(filenames)) == 2
    resolved = [Path(name) if Path(name).is_absolute() else Path(bot.FILES_DIR).parent / name for name in filenames]
    assert [path.read_text(encoding="utf-8") for path in resolved] == ["first report", "second report"]


def test_unknown_analysis_and_extra_keys_survive_save(sandbox):
    pid = patient()
    with sqlite3.connect(bot.DB_NAME) as connection:
        connection.execute("INSERT INTO analyses(pid,type,data) VALUES(?,?,?)", (pid, "Старый анализ", '{"Редкий показатель": "сохранить"}'))
        connection.execute("INSERT INTO analyses(pid,type,data) VALUES(?,?,?)", (pid, "ОАК", '{"Гемоглобин": "120", "Редкий показатель": "сохранить"}'))
    form = bot.AnalysisWindow(pid)
    assert any(block["type"] == "Старый анализ" for block in form.blocks)
    form.save()
    saved = rows("SELECT type,data FROM analyses ORDER BY id")
    assert len(saved) == 2
    assert all(json.loads(data)["Редкий показатель"] == "сохранить" for _, data in saved)


def test_copy_failure_keeps_dialog_and_database_unchanged(sandbox, monkeypatch):
    directory, messages = sandbox
    source = directory / "report.txt"
    source.write_text("synthetic", encoding="utf-8")
    pid = patient()
    form = bot.AnalysisWindow(pid)
    form.add_block("ОАК")
    monkeypatch.setattr(bot.QFileDialog, "getOpenFileName", lambda *args: (str(source), ""))
    form.handle_file(0)
    def fail_copy(source, destination):
        Path(destination).write_text("partial", encoding="utf-8")
        raise OSError("simulated copy failure")
    monkeypatch.setattr(bot.shutil, "copy2", fail_copy)
    form.save()
    assert form.result() != QDialog.DialogCode.Accepted
    assert rows("SELECT count(*) FROM analyses") == [(0,)]
    assert not list(Path(bot.FILES_DIR).glob("*"))
    assert messages


def test_database_failure_rolls_back_attachment_copy(sandbox, monkeypatch):
    directory, messages = sandbox
    source = directory / "report.txt"
    source.write_text("synthetic", encoding="utf-8")
    pid = patient()
    form = bot.AnalysisWindow(pid)
    form.add_block("ОАК")
    monkeypatch.setattr(bot.QFileDialog, "getOpenFileName", lambda *args: (str(source), ""))
    form.handle_file(0)
    def fail_save(*args):
        raise sqlite3.OperationalError("simulated database lock")
    monkeypatch.setattr(bot, "save_analyses", fail_save)
    form.save()
    assert form.result() != QDialog.DialogCode.Accepted
    assert rows("SELECT count(*) FROM analyses") == [(0,)]
    assert not list(Path(bot.FILES_DIR).glob("*"))
    assert messages


def test_malformed_legacy_json_remains_visible_and_unchanged(sandbox):
    pid = patient()
    with sqlite3.connect(bot.DB_NAME) as connection:
        connection.execute("INSERT INTO analyses(pid,type,data) VALUES(?,?,?)",
                           (pid, "ОАК", "{broken legacy value"))
    form = bot.AnalysisWindow(pid)
    assert len(form.blocks) == 1
    assert form.blocks[0]["malformed"]
    form.save()
    assert rows("SELECT data FROM analyses") == [("{broken legacy value",)]


def test_plus_select_type_then_save(sandbox):
    pid = patient()
    form = bot.AnalysisWindow(pid)
    form.on_add()
    form.save()
    assert rows("SELECT count(*) FROM analyses") == [(0,)]
    form.select_type(0, "ОАМ")
    form.t.item(0, 2).setText("Светло-жёлтый")
    form.save()
    assert rows("SELECT type FROM analyses") == [("ОАМ",)]
    assert json.loads(rows("SELECT data FROM analyses")[0][0])["Цвет"] == "Светло-жёлтый"


@pytest.mark.parametrize("birth,phone", [("31.02.2015", ""), ("01.01.2999", ""), ("15.06.2015", "123")])
def test_invalid_passport_does_not_write(sandbox, birth, phone):
    form = bot.PatientForm()
    form.last.setText("Тест")
    form.first.setText("Пример")
    form.birth.setText(birth)
    form.phone.setText(phone)
    form.save()
    assert rows("SELECT count(*) FROM patients") == [(0,)]


def test_readonly_save_handlers_cannot_write(sandbox):
    pid = patient()
    before = get_patient(pid)
    form = bot.PatientForm(before, readonly=True)
    form.last.setText("Нельзя сохранить")
    form.save()
    assert get_patient(pid) == before
    analyses = bot.AnalysisWindow(pid, readonly=True)
    analyses.add_block("ОАК", {"Гемоглобин": "999"})
    analyses.save()
    assert rows("SELECT count(*) FROM analyses") == [(0,)]
