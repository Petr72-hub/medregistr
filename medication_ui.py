"""Medication planning and pharmacy windows. Importing this module never opens a DB."""
from datetime import date
from decimal import Decimal
import sqlite3

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

import medication


DRUGS = ("Инфликсимаб", "Ведолизумаб", "Адалимумаб")
UNIT_MG = {"Инфликсимаб": 100, "Ведолизумаб": 300, "Адалимумаб": 40}
STATUS_LABELS = {"planned": "Запланировано", "issued": "Выдано", "cancelled": "Отменено"}
PHASE_LABELS = {"induction": "Индукция", "maintenance": "Поддержка", "manual": "Дополнительное"}


def confirm(parent, text):
    return QMessageBox.question(parent, "Подтверждение", text,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes


def date_editor(value=None):
    editor = QDateEdit(value or QDate.currentDate())
    editor.setCalendarPopup(True)
    editor.setDisplayFormat("dd.MM.yyyy")
    return editor


def iso(editor):
    return editor.date().toString("yyyy-MM-dd")


def display_date(value):
    if not value:
        return "—"
    parsed = QDate.fromString(str(value), "yyyy-MM-dd")
    return parsed.toString("dd.MM.yyyy") if parsed.isValid() else str(value)


def label(text, role=None):
    widget = QLabel(text)
    widget.setWordWrap(True)
    if role:
        widget.setProperty("role", role)
    return widget


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.verticalHeader().setVisible(False)
    widget.setAlternatingRowColors(False)
    widget.setShowGrid(False)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setAlternatingRowColors(True)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    widget.verticalHeader().setDefaultSectionSize(36)
    return widget


def set_cell(widget, row, column, value, numeric=False, editable=False, key=None):
    item = QTableWidgetItem(str(value) if value is not None else "—")
    flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
    if editable:
        flags |= Qt.ItemFlag.ItemIsEditable
    item.setFlags(flags)
    if numeric:
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if key is not None:
        item.setData(Qt.ItemDataRole.UserRole, key)
    widget.setItem(row, column, item)


class DateActionDialog(QDialog):
    def __init__(self, title, initial=None, allow_reset=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        self.date_edit = date_editor(QDate.fromString(initial, "yyyy-MM-dd") if initial else None)
        form = QFormLayout()
        form.addRow("Дата", self.date_edit)
        layout.addLayout(form)
        self.reset_box = QCheckBox("Пересчитать последующие даты от этой даты")
        self.reset_box.setVisible(allow_reset)
        layout.addWidget(self.reset_box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class ExtraEventDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Дополнительное введение")
        self.setMinimumWidth(470)
        layout = QVBoxLayout(self)
        layout.addWidget(label("Отдельное назначение врача. Оно резервирует препарат и не списывает остаток.", "muted"))
        form = QFormLayout()
        self.drug_box = QComboBox()
        self.drug_box.addItems(DRUGS)
        self.date_edit = date_editor()
        self.dose_spin = QDoubleSpinBox()
        self.dose_spin.setRange(0.01, 1000000)
        self.dose_spin.setDecimals(2)
        self.dose_spin.setSuffix(" мг")
        form.addRow("Препарат", self.drug_box)
        form.addRow("Дата введения", self.date_edit)
        form.addRow("Назначенная доза", self.dose_spin)
        layout.addLayout(form)
        self.reset_box = QCheckBox("Пересчитать последующие даты от этого введения")
        layout.addWidget(self.reset_box)
        layout.addWidget(label("Без отметки существующее расписание сохраняется.", "muted"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class TherapyPlanner(QDialog):
    def __init__(self, db_path, pid, readonly=False, parent=None):
        super().__init__(parent)
        self.db_path = str(db_path)
        self.pid = pid
        self.readonly = readonly
        self.preview_events = []
        self.events = []
        self._rendering_preview = False
        self._dirty = False
        self.setWindowTitle("Терапия — просмотр" if readonly else "Планирование терапии")
        self.resize(1100, 780)
        self.setMinimumSize(900, 620)
        medication.initialize_medication(self.db_path)
        connection = sqlite3.connect(self.db_path)
        try:
            patient = connection.execute("SELECT last,first,middle,birth,weight FROM patients WHERE id=?", (pid,)).fetchone()
        finally:
            connection.close()
        if patient is None:
            raise ValueError("Пациент не найден.")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.addWidget(label(" ".join(str(part) for part in patient[:3] if part), "title"))
        layout.addWidget(label(f"Дата рождения: {patient[3] or '—'} · План и фактическая выдача учитываются отдельно", "muted"))
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        current_weight = medication.get_patient_weight(self.db_path, self.pid)
        self._build_plan_tab(current_weight)
        self._build_events_tab()
        self._build_weight_tab(current_weight)
        self._build_legacy_tab()
        self.status_label = label("", "muted")
        layout.addWidget(self.status_label)
        bottom = QHBoxLayout()
        bottom.addStretch()
        close = QPushButton("Закрыть")
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        layout.addLayout(bottom)
        if readonly:
            self.tabs.setTabEnabled(0, False)
            self.tabs.setTabEnabled(2, False)
            self.tabs.setCurrentIndex(1)
        self.refresh_events()

    def _build_plan_tab(self, weight):
        page = QWidget()
        layout = QVBoxLayout(page)
        controls = QGridLayout()
        self.drug_box = QComboBox()
        self.drug_box.addItems(DRUGS)
        self.phase_box = QComboBox()
        self.phase_box.addItem("Индукция и последующая поддержка", "induction")
        self.phase_box.addItem("Только поддерживающая терапия", "maintenance")
        self.diagnosis_box = QComboBox()
        self.diagnosis_box.addItem("Болезнь Крона", "Crohn")
        self.diagnosis_box.addItem("Язвенный колит", "UC")
        self.weight_spin = QDoubleSpinBox()
        self.weight_spin.setDecimals(2)
        self.weight_spin.setRange(0, 10000)
        self.weight_spin.setSuffix(" кг")
        self.weight_spin.setValue(float(weight or 0))
        self.dose_box = QComboBox()
        self.interval_box = QComboBox()
        self.start_date = date_editor()
        self.end_date = date_editor(QDate.currentDate().addYears(1))
        self.maintenance_date = date_editor()
        self.custom_maintenance = QCheckBox("Изменить первую дату поддержки")
        self.maintenance_date.setEnabled(False)
        fields = [("Препарат", self.drug_box), ("Этап", self.phase_box),
                  ("Диагноз", self.diagnosis_box), ("Вес на начало", self.weight_spin),
                  ("Доза поддержки", self.dose_box), ("Интервал поддержки", self.interval_box),
                  ("Начало курса", self.start_date), ("Планировать до", self.end_date)]
        for index, (title, widget) in enumerate(fields):
            row, column = divmod(index, 4)
            cell = QVBoxLayout()
            cell.addWidget(label(title, "muted"))
            cell.addWidget(widget)
            controls.addLayout(cell, row, column)
        controls.addWidget(self.custom_maintenance, 2, 0, 1, 2)
        controls.addWidget(self.maintenance_date, 2, 2, 1, 2)
        self.intensive_box = QCheckBox("Интенсификация инфликсимаба — 10 мг/кг")
        self.intensive_box.toggled.connect(lambda checked: self.dose_box.setCurrentIndex(1 if checked else 0) if self.drug_box.currentText() == "Инфликсимаб" else None)
        self.dose_box.currentIndexChanged.connect(lambda index: self.intensive_box.setChecked(index == 1) if self.drug_box.currentText() == "Инфликсимаб" else None)
        controls.addWidget(self.intensive_box, 3, 0, 1, 2)
        self.induction_end = date_editor()
        self.induction_end.setAccessibleName("Дата окончания индукции")
        controls.addWidget(label("Окончание индукции (для поддержки)", "muted"), 3, 2)
        controls.addWidget(self.induction_end, 3, 3)
        self.induction_end.dateChanged.connect(self._from_induction_end)
        self.interval_box.currentIndexChanged.connect(self._from_induction_end)
        layout.addLayout(controls)
        self.rule_label = label("", "notice")
        layout.addWidget(self.rule_label)
        actions = QHBoxLayout()
        self.preview_button = QPushButton("Рассчитать расписание")
        self.preview_button.setProperty("role", "primary")
        self.preview_button.clicked.connect(self.preview_plan)
        self.save_button = QPushButton("Сохранить план")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save_plan)
        actions.addWidget(self.preview_button)
        actions.addWidget(self.save_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.replace_box = QCheckBox("Заменить будущий план этого препарата с даты начала курса")
        layout.addWidget(self.replace_box)
        layout.addWidget(label("Вес в строках индукции можно уточнить двойным нажатием. Доза и количество пересчитаются.", "muted"))
        self.preview_table = table(["Дата", "Этап", "Вес, кг", "Доза, мг", "Форма, мг", "Единиц", "Остаток, мг"])
        self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.preview_table.itemChanged.connect(self._preview_weight_changed)
        layout.addWidget(self.preview_table)
        self.tabs.addTab(page, "План курса")
        self.drug_box.currentTextChanged.connect(self._configure_drug)
        self.phase_box.currentIndexChanged.connect(self._update_rule)
        self.diagnosis_box.currentIndexChanged.connect(self._update_rule)
        self.custom_maintenance.toggled.connect(self._toggle_maintenance)
        self.start_date.dateChanged.connect(self._suggest_maintenance)
        self.interval_box.currentIndexChanged.connect(self._suggest_maintenance)
        for widget in (self.phase_box, self.diagnosis_box, self.dose_box, self.interval_box):
            widget.currentIndexChanged.connect(self._invalidate_preview)
        for widget in (self.start_date, self.end_date, self.maintenance_date):
            widget.dateChanged.connect(self._invalidate_preview)
        self.weight_spin.valueChanged.connect(self._invalidate_preview)
        self._configure_drug()

    def _build_events_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(label("План резервирует препарат. Остаток уменьшается только после подтверждённой выдачи.", "notice"))
        actions = QHBoxLayout()
        self.issue_button = QPushButton("Выдать препарат")
        self.issue_button.setProperty("role", "primary")
        self.move_button = QPushButton("Перенести дату")
        self.cancel_button = QPushButton("Отменить введение")
        self.extra_button = QPushButton("Дополнительное введение")
        for button, handler in [(self.issue_button, self.issue_selected), (self.move_button, self.reschedule_selected),
                                (self.cancel_button, self.cancel_selected), (self.extra_button, self.add_extra)]:
            button.clicked.connect(handler)
            button.setEnabled(not self.readonly)
            actions.addWidget(button)
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(self.refresh_events)
        actions.addWidget(refresh)
        layout.addLayout(actions)
        self.events_table = table(["Дата", "Препарат", "Этап", "Доза, мг", "Единиц", "Статус", "Выдано"])
        layout.addWidget(self.events_table)
        self.tabs.addTab(page, "Назначения и выдача")

    def _build_weight_tab(self, weight):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(label("Изменение веса", "section"))
        layout.addWidget(label("Укажите дату, с которой известен новый вес. Пересчитываются только запланированные введения с этой даты; выданные записи сохраняются.", "muted"))
        form = QFormLayout()
        self.effective_date = date_editor()
        self.new_weight_spin = QDoubleSpinBox()
        self.new_weight_spin.setRange(0.01, 10000)
        self.new_weight_spin.setDecimals(2)
        self.new_weight_spin.setSuffix(" кг")
        self.new_weight_spin.setValue(float(weight or 0.01))
        form.addRow("Новый вес", self.new_weight_spin)
        form.addRow("Действует с", self.effective_date)
        layout.addLayout(form)
        self.weight_button = QPushButton("Применить вес к будущим введениям")
        self.weight_button.setProperty("role", "primary")
        self.weight_button.setEnabled(not self.readonly)
        self.weight_button.clicked.connect(self.update_weight)
        layout.addWidget(self.weight_button)
        layout.addStretch()
        self.tabs.addTab(page, "Вес пациента")

    def _configure_drug(self, *_):
        drug = self.drug_box.currentText()
        self.intensive_box.setVisible(drug == "Инфликсимаб")
        self.dose_box.clear()
        self.interval_box.clear()
        doses = (20, 40) if drug == "Адалимумаб" else (5, 10)
        for dose in doses:
            self.dose_box.addItem(f"{dose} мг" if drug == "Адалимумаб" else f"{dose} мг/кг", str(dose))
        intervals = (1, 2) if drug == "Адалимумаб" else ((4, 8) if drug == "Ведолизумаб" else (4, 6, 8))
        for interval in intervals:
            self.interval_box.addItem(f"{interval} нед.", interval)
        self.interval_box.setCurrentIndex(len(intervals) - 1)
        self._update_rule()
        self._suggest_maintenance()
        self._invalidate_preview()

    def _from_induction_end(self, *_):
        if self.phase_box.currentData() == "maintenance" and self.interval_box.currentData():
            self.start_date.setDate(self.induction_end.date().addDays(int(self.interval_box.currentData()) * 7))

    def _update_rule(self, *_):
        drug = self.drug_box.currentText()
        induction = self.phase_box.currentData() == "induction"
        uc = self.diagnosis_box.currentData() == "UC"
        limited = drug == "Адалимумаб" and uc
        self.diagnosis_box.setEnabled(drug == "Адалимумаб")
        self.dose_box.setEnabled(not limited)
        self.interval_box.setEnabled(not limited)
        self.custom_maintenance.setEnabled(induction and not limited)
        self.maintenance_date.setEnabled(induction and not limited and self.custom_maintenance.isChecked())
        self.preview_button.setEnabled(not self.readonly and not (limited and not induction))
        if limited:
            text = "Для ЯК доступна только индукция адалимумаба; поддержка в локальном справочнике не задана."
        elif drug == "Адалимумаб":
            text = "Индукция: дни 0 и 14; вес от 40 кг относится к старшей весовой группе. Поддержка — фиксированная доза 20 или 40 мг по назначению врача."
        elif drug == "Ведолизумаб":
            text = "Индукция: дни 0, 14, 42; от 30 кг — 5 мг/кг, до 30 кг — 10 мг/кг. Дозу и интервал поддержки выбирает врач."
        else:
            text = "Индукция: дни 0, 14, 42 по 5 мг/кг. Поддержка — 5 мг/кг; интенсификация — 10 мг/кг по назначению врача."
        self.rule_label.setText(text)
        self._suggest_maintenance()

    def _toggle_maintenance(self, checked):
        self.maintenance_date.setEnabled(checked and self.custom_maintenance.isEnabled())
        self._suggest_maintenance()
        self._invalidate_preview()

    def _suggest_maintenance(self, *_):
        if not self.custom_maintenance.isChecked():
            last_day = 14 if self.drug_box.currentText() == "Адалимумаб" else 42
            interval = self.interval_box.currentData() or 0
            self.maintenance_date.setDate(self.start_date.date().addDays(last_day + 7 * interval))

    def _invalidate_preview(self, *_):
        if hasattr(self, "save_button"):
            self.save_button.setEnabled(False)
        if self.preview_events:
            self._dirty = True

    def _error(self, exc):
        QMessageBox.warning(self, "Не удалось выполнить действие", str(exc))

    def preview_plan(self):
        if self.readonly:
            return
        try:
            self.preview_events = medication.preview_schedule(
                self.drug_box.currentText(), iso(self.start_date), str(self.weight_spin.value()),
                phase=self.phase_box.currentData(), dose_choice=self.dose_box.currentData(),
                interval_weeks=self.interval_box.currentData(),
                maintenance_start=iso(self.maintenance_date) if self.custom_maintenance.isChecked() else None,
                diagnosis=self.diagnosis_box.currentData(), through_date=iso(self.end_date))
            self._render_preview()
            self._dirty = True
            self.save_button.setEnabled(bool(self.preview_events))
            units = sum(event["units"] for event in self.preview_events)
            self.status_label.setText(f"Расписание: {len(self.preview_events)} введений, {units} единиц препарата. План ещё не сохранён.")
        except (ValueError, TypeError, ArithmeticError, sqlite3.Error, OSError) as exc:
            self._error(exc)

    def _render_preview(self):
        self._rendering_preview = True
        try:
            self.preview_table.setRowCount(len(self.preview_events))
            for row, event in enumerate(self.preview_events):
                values = [display_date(event["date"]), PHASE_LABELS.get(event["phase"], event["phase"]),
                          event.get("weight_kg"), event["dose_mg"], event["unit_mg"], event["units"],
                          Decimal(str(event["unit_mg"])) * event["units"] - Decimal(str(event["dose_mg"]))]
                for column, value in enumerate(values):
                    set_cell(self.preview_table, row, column, value, numeric=column >= 2,
                             editable=column == 2 and event["phase"] == "induction" and not self.readonly)
        finally:
            self._rendering_preview = False

    def _preview_weight_changed(self, item):
        if self._rendering_preview or self.readonly or item.column() != 2:
            return
        try:
            index = item.row()
            self.preview_events[index] = medication.recalculate_event(self.preview_events[index], item.text().replace(",", "."))
            self._dirty = True
        except (ValueError, TypeError, ArithmeticError) as exc:
            self._error(exc)
        self._render_preview()

    def save_plan(self):
        if self.readonly or not self.preview_events or not self.save_button.isEnabled():
            return
        if not confirm(self, "Сохранить показанные введения в план? Они зарезервируют препарат; списание произойдёт только при выдаче."):
            return
        try:
            if self.replace_box.isChecked():
                medication.replace_plan(self.db_path, self.pid, self.preview_events, iso(self.start_date))
            else:
                medication.save_plan(self.db_path, self.pid, self.preview_events)
            self._dirty = False
            self.save_button.setEnabled(False)
            self.refresh_events()
            self.tabs.setCurrentIndex(1)
            self.status_label.setText("План сохранён. Препарат зарезервирован, выдача ещё не выполнена.")
        except (ValueError, TypeError, ArithmeticError, sqlite3.Error, OSError) as exc:
            self._error(exc)

    def refresh_events(self):
        try:
            self.events = medication.list_events(self.db_path, self.pid)
            self.events_table.setRowCount(len(self.events))
            for row, event in enumerate(self.events):
                values = [display_date(event["date"]), event["drug"],
                          "Дополнительное" if event.get("extra") else PHASE_LABELS.get(event["phase"], event["phase"]),
                          event["dose_mg"], event["units"], STATUS_LABELS.get(event["status"], event["status"]),
                          display_date(event.get("issued_on"))]
                for column, value in enumerate(values):
                    set_cell(self.events_table, row, column, value, numeric=column in (3, 4), key=event["id"])
        except (sqlite3.Error, OSError, ValueError) as exc:
            self._error(exc)

    def _build_legacy_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(label("Старый журнал — сохранённая история", "section"))
        note = label("Эти записи учитываются по своим датам. Новые назначения создавайте в плане; старые записи не выдаются повторно.", "notice")
        note.setWordWrap(True)
        layout.addWidget(note)
        history = table(["Дата", "Препарат", "Этап", "Доза, мг", "Единиц", "Комментарий"])
        con = sqlite3.connect(self.db_path)
        try:
            rows = con.execute("SELECT date,drug,stage,total_mg_needed,vials_count,comment FROM therapy WHERE pid=? ORDER BY id", (self.pid,)).fetchall()
        finally:
            con.close()
        history.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for col, value in enumerate(values):
                set_cell(history, row, col, value if value is not None else "—", numeric=col in (3, 4))
        layout.addWidget(history)
        self.tabs.addTab(page, "Старый журнал")

    def _selected(self, require_planned=True):
        row = self.events_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Назначения", "Выберите введение в таблице.")
            return None
        event_id = self.events_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        event = next((entry for entry in self.events if entry["id"] == event_id), None)
        if event is None or (require_planned and event["status"] != "planned"):
            QMessageBox.information(self, "Назначения", "Это действие доступно только для запланированного введения.")
            return None
        return event

    def issue_selected(self):
        if self.readonly:
            return
        event = self._selected()
        if event is None:
            return
        dialog = DateActionDialog("Фактическая выдача", parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not confirm(self, f"Подтвердить фактическую выдачу {event['drug']}: {event['units']} ед. на {display_date(iso(dialog.date_edit))}? Остаток уменьшится."):
            return
        try:
            medication.issue_event(self.db_path, self.pid, event["id"], iso(dialog.date_edit))
            self.refresh_events()
            self.status_label.setText("Выдача зарегистрирована. Повторное списание этой записи исключено.")
        except (ValueError, sqlite3.Error, OSError) as exc:
            self._error(exc)

    def cancel_selected(self):
        if self.readonly:
            return
        event = self._selected()
        if event is None or not confirm(self, "Отменить выбранное введение и снять его резерв?"):
            return
        try:
            medication.cancel_event(self.db_path, self.pid, event["id"])
            self.refresh_events()
        except (ValueError, sqlite3.Error, OSError) as exc:
            self._error(exc)

    def reschedule_selected(self):
        if self.readonly:
            return
        event = self._selected()
        if event is None:
            return
        dialog = DateActionDialog("Перенос введения", event["date"], allow_reset=True, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not confirm(self, "Сохранить перенос выбранного введения?"):
            return
        try:
            medication.reschedule_event(self.db_path, self.pid, event["id"], iso(dialog.date_edit), reset_cadence=dialog.reset_box.isChecked())
            self.refresh_events()
        except (ValueError, sqlite3.Error, OSError) as exc:
            self._error(exc)

    def add_extra(self):
        if self.readonly:
            return
        dialog = ExtraEventDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not confirm(self, "Добавить дополнительное введение в план?"):
            return
        try:
            event = medication.manual_event(dialog.drug_box.currentText(), iso(dialog.date_edit), str(dialog.dose_spin.value()), extra=True)
            medication.save_extra_event(self.db_path, self.pid, event, reset_cadence=dialog.reset_box.isChecked())
            self.refresh_events()
        except (ValueError, TypeError, ArithmeticError, sqlite3.Error, OSError) as exc:
            self._error(exc)

    def update_weight(self):
        if self.readonly or not confirm(self, "Применить новый вес к запланированным введениям с выбранной даты?"):
            return
        try:
            medication.update_pending_weight(self.db_path, self.pid, iso(self.effective_date), str(self.new_weight_spin.value()))
            self.refresh_events()
            self.status_label.setText("Вес обновлён. Выданные записи сохранены без изменений.")
        except (ValueError, TypeError, ArithmeticError, sqlite3.Error, OSError) as exc:
            self._error(exc)

    def reject(self):
        if not self._dirty or confirm(self, "Закрыть окно без сохранения рассчитанного плана?"):
            super().reject()

    def closeEvent(self, event):
        if not self._dirty or confirm(self, "Закрыть окно без сохранения рассчитанного плана?"):
            event.accept()
        else:
            event.ignore()


class PharmacyWindow(QDialog):
    def __init__(self, db_path, parent=None):
        super().__init__(parent)
        self.db_path = str(db_path)
        medication.initialize_medication(self.db_path)
        self.setWindowTitle("Аптека и потребность в препаратах")
        self.resize(1160, 760)
        self.setMinimumSize(900, 620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.addWidget(label("Аптека и потребность", "title"))
        layout.addWidget(label("Учёт в целых флаконах и шприцах. Резерв по плану и страховой запас показаны отдельно.", "muted"))
        tabs = QTabWidget()
        layout.addWidget(tabs)
        forecast_page = QWidget()
        forecast_layout = QVBoxLayout(forecast_page)
        top = QHBoxLayout()
        top.addWidget(label("Рассчитать по дату включительно"))
        self.forecast_date = date_editor(QDate.currentDate().addYears(1))
        top.addWidget(self.forecast_date)
        refresh = QPushButton("Рассчитать потребность")
        refresh.setProperty("role", "primary")
        refresh.clicked.connect(self.refresh)
        top.addWidget(refresh)
        top.addStretch()
        forecast_layout.addLayout(top)
        self.forecast_table = table(["Препарат", "Сейчас", "Резерв", "Страховой", "После плана", "Свободно", "К закупке", "Дефицит с"])
        self.forecast_table.setMaximumHeight(190)
        forecast_layout.addWidget(self.forecast_table)
        self.notice = label("", "notice")
        forecast_layout.addWidget(self.notice)
        timeline_row = QHBoxLayout()
        timeline_row.addWidget(label("Календарь потребности", "section"))
        self.timeline_drug = QComboBox()
        self.timeline_drug.addItems(DRUGS)
        self.timeline_drug.currentTextChanged.connect(self._render_timeline)
        timeline_row.addWidget(self.timeline_drug)
        timeline_row.addStretch()
        forecast_layout.addLayout(timeline_row)
        self.timeline_table = table(["Дата", "Поступление, ед.", "Введение, ед.", "Остаток по плану", "Свободно сверх страхового"])
        forecast_layout.addWidget(self.timeline_table)
        tabs.addTab(forecast_page, "Потребность")
        receipts_page = QWidget()
        receipts_layout = QVBoxLayout(receipts_page)
        form = QGridLayout()
        self.drug_box = QComboBox()
        self.drug_box.addItems(DRUGS)
        self.form_label = label("")
        self.drug_box.currentTextChanged.connect(self._show_form)
        self.quantity_spin = QSpinBox()
        self.quantity_spin.setRange(1, 100000000)
        self.receipt_date = date_editor()
        self.receipt_comment = QLineEdit()
        self.receipt_comment.setPlaceholderText("Накладная или комментарий")
        form.addWidget(label("Препарат", "muted"), 0, 0)
        form.addWidget(label("Количество единиц", "muted"), 0, 1)
        form.addWidget(label("Дата поступления", "muted"), 0, 2)
        form.addWidget(self.drug_box, 1, 0)
        form.addWidget(self.quantity_spin, 1, 1)
        form.addWidget(self.receipt_date, 1, 2)
        form.addWidget(self.form_label, 2, 0)
        form.addWidget(self.receipt_comment, 2, 1, 1, 2)
        receipts_layout.addLayout(form)
        add = QPushButton("Зарегистрировать поступление")
        add.setProperty("role", "primary")
        add.clicked.connect(self.add_receipt)
        receipts_layout.addWidget(add)
        self.receipts_table = table(["Дата", "Препарат", "Единиц", "Комментарий"])
        receipts_layout.addWidget(self.receipts_table)
        tabs.addTab(receipts_page, "Поступления")
        safety_page = QWidget()
        safety_layout = QVBoxLayout(safety_page)
        safety_layout.addWidget(label("Страховой запас", "section"))
        safety_layout.addWidget(label("Дополнительный целевой запас в единицах препарата. Он не является выдачей или резервом конкретному пациенту.", "muted"))
        safety_form = QFormLayout()
        self.safety_drug = QComboBox()
        self.safety_drug.addItems(DRUGS)
        self.safety_spin = QSpinBox()
        self.safety_spin.setRange(0, 100000000)
        self.safety_drug.currentTextChanged.connect(self._show_safety)
        safety_form.addRow("Препарат", self.safety_drug)
        safety_form.addRow("Страховой запас, единиц", self.safety_spin)
        safety_layout.addLayout(safety_form)
        safety_button = QPushButton("Сохранить страховой запас")
        safety_button.setProperty("role", "primary")
        safety_button.clicked.connect(self.save_safety)
        safety_layout.addWidget(safety_button)
        safety_layout.addStretch()
        tabs.addTab(safety_page, "Страховой запас")
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        self.metrics = {}
        self._show_form()
        self.refresh()

    def _show_form(self, *_):
        drug = self.drug_box.currentText()
        noun = "шприц" if drug == "Адалимумаб" else "флакон"
        self.form_label.setText(f"1 {noun} = {UNIT_MG[drug]} мг")

    def _error(self, exc):
        QMessageBox.warning(self, "Не удалось выполнить действие", str(exc))

    def refresh(self):
        self.metrics = {}
        self.forecast_table.setRowCount(0)
        self.timeline_table.setRowCount(0)
        self.notice.setText("Расчёт обновляется…")
        try:
            self.metrics = medication.forecast(self.db_path, iso(self.forecast_date))
            self.forecast_table.setRowCount(len(DRUGS))
            legacy = 0
            for row, drug in enumerate(DRUGS):
                metric = self.metrics[drug]
                values = [drug, metric["current_balance"], metric["reserve"], metric["safety_stock"],
                          metric["projected_balance"], metric["free"], metric["shortage"], display_date(metric["first_stockout_date"])]
                for column, value in enumerate(values):
                    set_cell(self.forecast_table, row, column, value, numeric=1 <= column <= 6)
                old = metric.get("legacy_future_entries", 0)
                legacy += len(old) if isinstance(old, list) else int(old or 0)
            self.notice.setText("В старом журнале есть будущие записи: проверьте их отдельно; они не превращаются в фактическую выдачу автоматически." if legacy else
                                "Поступления учитываются по дате. «К закупке» включает потребность плана и заданный страховой запас.")
            overdue = sum(m.get("overdue_units", 0) for m in self.metrics.values())
            if overdue:
                self.notice.setText(self.notice.text() + f" Просроченный план: {overdue} ед.; резерв сохранён до выдачи или отмены.")
            self._render_timeline()
            self._show_safety()
            receipts = medication.list_receipts(self.db_path)
            self.receipts_table.setRowCount(len(receipts))
            for row, receipt in enumerate(receipts):
                for column, value in enumerate([display_date(receipt["date"]), receipt["drug"], receipt["units"], receipt.get("comment", "")]):
                    set_cell(self.receipts_table, row, column, value, numeric=column == 2)
        except (ValueError, TypeError, ArithmeticError, sqlite3.Error, OSError) as exc:
            self._error(exc)

    def _render_timeline(self, *_):
        metrics = self.metrics.get(self.timeline_drug.currentText(), {})
        rows = metrics.get("timeline", [])
        self.timeline_table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            for column, value in enumerate([display_date(item["date"]), item["receipt_units"], item["reserved_units"], item["projected_balance"], item["free"]]):
                set_cell(self.timeline_table, row, column, value, numeric=column > 0)

    def _show_safety(self, *_):
        self.safety_spin.setValue(int(self.metrics.get(self.safety_drug.currentText(), {}).get("safety_stock", 0)))

    def add_receipt(self):
        if not confirm(self, "Сохранить поступление препарата с указанной датой и количеством единиц?"):
            return
        try:
            medication.add_receipt(self.db_path, self.drug_box.currentText(), iso(self.receipt_date), self.quantity_spin.value(), comment=self.receipt_comment.text().strip())
            self.refresh()
            self.receipt_comment.clear()
        except (ValueError, TypeError, sqlite3.Error, OSError) as exc:
            self._error(exc)

    def save_safety(self):
        if not confirm(self, "Сохранить страховой запас для выбранного препарата?"):
            return
        try:
            medication.set_safety_stock(self.db_path, self.safety_drug.currentText(), self.safety_spin.value())
            self.refresh()
        except (ValueError, TypeError, sqlite3.Error, OSError) as exc:
            self._error(exc)
