# UPDATED MEDICAL SYSTEM (MedRegister)
import sys, os, shutil, sqlite3, re, json, math
from datetime import datetime
from functools import partial
from PyQt6.QtWidgets import *
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices

from uuid import uuid4
from storage import resolve_paths, prepare_database, save_analyses

DB_NAME, FILES_DIR = resolve_paths()

from diagnoses import MKB_CODES

CLINICS = ["Поликлиника №1", "Поликлиника №2", "Поликлиника №5"]

# Явный список колонок пациента и их порядок. Используется во всех SELECT-запросах
# вместо "SELECT *", чтобы порядок полей в результирующем кортеже не зависел от того,
# в каком физическом порядке колонки лежат в таблице (ALTER TABLE всегда добавляет
# новые колонки в конец таблицы, а не туда, где они "логически" должны быть).
PATIENT_COLUMNS = ("id, last, first, middle, birth, mkb, gender, "
                   "address_reg, address_fact, clinic, uch, doctor, phone, invalid, weight")

# =========================
# ПРЕПАРАТЫ (данные из preparaty.xlsx, лист "расчеты")
# =========================
# Каждый препарат описывает:
#   vial_mg              - сколько мг действующего вещества в 1 флаконе/шприце
#   requires_diagnosis    - нужно ли спрашивать диагноз (ЯК/БК) для расчёта дозы
#   induction             - схема индукции: недели введения + правило расчёта дозы
#   maintenance_options    - варианты поддерживающей терапии (столбцы H, J-L)
#   intensification_options- варианты интенсификации (столбец I), либо None если "нет"
#
# Правила расчёта дозы (dose_rule):
#   ("mg_per_kg", X)                -> доза = X мг * вес пациента (кг)
#   ("weight_band_mg_per_kg", [...])-> доза мг/кг зависит от порога веса (Ведолизумаб)
#   ("diagnosis_weight_flat", {...})-> фиксированная доза в мг по диагнозу+весу (Адалимумаб)

DRUGS = {
    "Инфликсимаб": {
        "form": "Лиофилизат для приготовления раствора для инфузий 100 мг: фл. 1 шт.",
        "vial_mg": 100,
        "vial_unit_name": "флакон",
        "requires_diagnosis": False,
        "induction": {
            "weeks": [0, 2, 6],
            "dose_rule": ("mg_per_kg", 5),
        },
        "maintenance_options": [
            {"label": "5 мг/кг, 1 раз в 8 недель", "dose_rule": ("mg_per_kg", 5), "interval_weeks": 8},
            {"label": "5 мг/кг, 1 раз в 6 недель", "dose_rule": ("mg_per_kg", 5), "interval_weeks": 6},
            {"label": "5 мг/кг, 1 раз в 4 недели", "dose_rule": ("mg_per_kg", 5), "interval_weeks": 4},
        ],
        "intensification_options": [
            {"label": "10 мг/кг, 1 раз в 8 недель", "dose_rule": ("mg_per_kg", 10), "interval_weeks": 8},
            {"label": "10 мг/кг, 1 раз в 6 недель", "dose_rule": ("mg_per_kg", 10), "interval_weeks": 6},
            {"label": "10 мг/кг, 1 раз в 4 недели", "dose_rule": ("mg_per_kg", 10), "interval_weeks": 4},
        ],
    },
    "Ведолизумаб": {
        "form": "Лиофилизат для приготовления концентрата для приготовления раствора для инфузий 300 мг: фл. 1 шт.",
        "vial_mg": 300,
        "vial_unit_name": "флакон",
        "requires_diagnosis": False,
        "induction": {
            "weeks": [0, 2, 6],
            # доза индукции определяется тем же правилом по весу, что и поддерживающая
            "dose_rule": ("weight_band_mg_per_kg", [
                {"min_kg": 30, "dose_mg_per_kg": 5, "interval_weeks": 8},
                {"max_kg": 30, "dose_mg_per_kg": 10, "interval_weeks": 4},
            ]),
        },
        "maintenance_options": [
            {"label": "Автоматически по весу (>30 кг: 5 мг/кг/8 нед.; <30 кг: 10 мг/кг/4 нед.)",
             "dose_rule": ("weight_band_mg_per_kg", [
                 {"min_kg": 30, "dose_mg_per_kg": 5, "interval_weeks": 8},
                 {"max_kg": 30, "dose_mg_per_kg": 10, "interval_weeks": 4},
             ]), "interval_weeks": None},
        ],
        "intensification_options": None,  # "нет" в таблице
    },
    "Адалимумаб": {
        "form": "Раствор для подкожного введения 40 мг/0.8 мл - 1 шприц",
        "vial_mg": 40,
        "vial_unit_name": "шприц",
        "requires_diagnosis": True,
        "diagnoses": ["ЯК", "БК"],
        "induction": {
            "weeks": [0, 2],
            # доза по неделям зависит от диагноза и веса; заполняется по выбору диагноза
            "dose_by_week": {
                "ЯК": {">40": [160, 80], "<40": [80, 40]},
                "БК": {">40": [160, 80], "<40": [80, 40]},
            },
            "weight_threshold": 40,
        },
        "maintenance_options": [
            {"label": "1 раз в 2 недели (стандартная)", "interval_weeks": 2,
             "dose_flat": {"ЯК": {">40": None, "<40": None}, "БК": {">40": 40, "<40": 20}}},
        ],
        "intensification_options": [
            {"label": "1 раз в неделю (интенсификация - сокращение интервала)", "interval_weeks": 1,
             "dose_flat": {"ЯК": {">40": None, "<40": None}, "БК": {">40": 40, "<40": 20}}},
        ],
    },
}

STAGES = ["Индукция", "Поддерживающая терапия", "Интенсификация"]

# =========================
# ОФОРМЛЕНИЕ
# =========================
from design import STYLE_SHEET, configure_app


def calc_vials(total_mg, vial_mg):
    """Считает минимальное число флаконов/шприцов, фактически введённую дозу и остаток (списание)."""
    if not total_mg or total_mg <= 0:
        return 0, 0, 0
    vials = math.ceil(round(total_mg, 4) / vial_mg)
    administered = vials * vial_mg
    waste = round(administered - total_mg, 2)
    return vials, administered, waste

# Определения анализов: название -> список (подпункт, единица измерения)
ANALYSIS_DEFS = {
    "ОАК": [
        ("Гемоглобин", "г/л"),
        ("Гематокрит", "%"),
        ("Эритроциты", "*10^12"),
        ("Цветовой показатель", ""),
        ("Ретикулоциты", "%"),
        ("Тромбоциты", "*10^9 Л"),
        ("СОЭ", "мм/ч"),
        ("Лейкоциты", "*10^9 Л"),
        ("Палочкоядерные гранулоциты", "%"),
        ("Сегментоядерные гранулоциты", "%"),
        ("Эозинофилы", "%"),
        ("Базофилы", "%"),
        ("Лимфоциты", "%"),
        ("Моноциты", "%"),
    ],
    "ОАМ": [
        ("Цвет", ""),
        ("Прозрачность", ""),
        ("Плотность", ""),
        ("Реакция pH", ""),
        ("Запах", ""),
        ("Белок", "г/л"),
        ("Глюкоза", "ммоль/л"),
        ("Кетоновые тела", ""),
        ("Билирубин", ""),
        ("Гемоглобин", ""),
        ("Лейкоциты", "в П/З"),
        ("Эритроциты", "в П/З"),
        ("Эпителий", ""),
        ("Цилиндры", ""),
        ("Соли", ""),
        ("Бактерии и нитраты", ""),
        ("Грибок", ""),
    ],
    "Биохимия крови": [
        ("Сахар крови", "ммоль/л"),
        ("Креатинин", "ммоль/л"),
        ("Мочевина", "ммоль/л"),
        ("Билирубин общий", "ммоль/л"),
        ("АлАТ", "МЕ/л"),
        ("АсАт", "МЕ/л"),
        ("Общий белок", "г/л"),
        ("Амилаза", "МЕ/л"),
        ("Холестерин", "ммоль/л"),
        ("Щелочная фосфатаза", "МЕ/л"),
        ("Кальций", "ммоль/л"),
        ("Калий", "ммоль/л"),
        ("Фосфор", "ммоль/л"),
        ("Натрий", "ммоль/л"),
        ("Хлор", "ммоль/л"),
        ("КФК", "МЕ/л"),
        ("ГГТ", "ед."),
    ],
}


# =========================
# DB
# =========================
def init_db():
    prepare_database(DB_NAME)
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS patients(
        id INTEGER PRIMARY KEY,
        last TEXT, first TEXT, middle TEXT,
        birth TEXT, mkb TEXT,
        gender TEXT,
        address_reg TEXT, address_fact TEXT,
        clinic TEXT, uch TEXT,
        doctor TEXT, phone TEXT,
        invalid INTEGER
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS analyses(
        id INTEGER PRIMARY KEY,
        pid INTEGER,
        type TEXT,
        data TEXT,
        file TEXT,
        created_at TEXT
    )
    """)
    # миграция analyses: докатываем недостающие колонки (например "type" в очень старых базах)
    cur.execute("PRAGMA table_info(analyses)")
    a_cols = [c[1] for c in cur.fetchall()]
    for col_name, col_type in [("pid", "INTEGER"), ("type", "TEXT"), ("data", "TEXT"),
                                ("file", "TEXT"), ("created_at", "TEXT")]:
        if col_name not in a_cols:
            cur.execute(f"ALTER TABLE analyses ADD COLUMN {col_name} {col_type}")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS therapy(
        id INTEGER PRIMARY KEY,
        pid INTEGER,
        date TEXT,
        drug TEXT,
        diagnosis TEXT,
        stage TEXT,
        regimen_label TEXT,
        weight_kg REAL,
        dose_value REAL,
        dose_unit TEXT,
        interval_weeks REAL,
        total_mg_needed REAL,
        vial_mg REAL,
        vials_count INTEGER,
        total_mg_administered REAL,
        waste_mg REAL,
        comment TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS stock_receipts(
        id INTEGER PRIMARY KEY,
        date TEXT,
        drug TEXT,
        vials_count INTEGER,
        vial_mg REAL,
        comment TEXT
    )
    """)
    # миграция: докатываем недостающие колонки в старых базах (например, если patients.db
    # создавалась более ранней версией программы)
    cur.execute("PRAGMA table_info(patients)")
    cols = [c[1] for c in cur.fetchall()]
    required_columns = [
        ("last", "TEXT"), ("first", "TEXT"), ("middle", "TEXT"),
        ("birth", "TEXT"), ("mkb", "TEXT"), ("gender", "TEXT"),
        ("address_reg", "TEXT"), ("address_fact", "TEXT"),
        ("clinic", "TEXT"), ("uch", "TEXT"), ("doctor", "TEXT"),
        ("phone", "TEXT"), ("invalid", "INTEGER"), ("weight", "REAL"),
    ]
    for col_name, col_type in required_columns:
        if col_name not in cols:
            cur.execute(f"ALTER TABLE patients ADD COLUMN {col_name} {col_type}")
    con.commit()
    con.close()
    from medication import initialize_medication
    initialize_medication(DB_NAME)


def confirm(parent, text):
    return QMessageBox.question(
        parent, "Подтверждение", text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    ) == QMessageBox.StandardButton.Yes


def parse_date(text):
    """Парсит дату вида ДД.ММ.ГГГГ, возвращает datetime или None, если не удалось."""
    text = (text or "").strip()
    try:
        return datetime.strptime(text, "%d.%m.%Y")
    except ValueError:
        return None


# =========================
# СКЛАД / ОТЧЁТЫ - вспомогательные расчёты
# =========================
def get_stock_summary():
    """Возвращает список словарей по каждому препарату:
    получено (флаконов/мг), списано по назначениям (флаконов/мг), остаток (флаконов/мг)."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()

    received = {}
    cur.execute("SELECT drug, SUM(vials_count) FROM stock_receipts GROUP BY drug")
    for drug, total in cur.fetchall():
        received[drug] = total or 0

    used = {}
    cur.execute("SELECT drug, SUM(vials_count) FROM therapy GROUP BY drug")
    for drug, total in cur.fetchall():
        used[drug] = total or 0

    con.close()

    result = []
    for drug, cfg in DRUGS.items():
        vial_mg = cfg["vial_mg"]
        rec = received.get(drug, 0)
        usd = used.get(drug, 0)
        balance = rec - usd
        result.append({
            "drug": drug,
            "vial_mg": vial_mg,
            "received_vials": rec,
            "received_mg": rec * vial_mg,
            "used_vials": usd,
            "used_mg": usd * vial_mg,
            "balance_vials": balance,
            "balance_mg": balance * vial_mg,
        })
    return result


def get_forecast(target_date):
    """Compatibility view over the dated plan and actual stock ledger."""
    from medication import forecast
    return [{"drug": drug, "current_balance_vials": m["current_balance"],
             "needed_vials_by_date": m["reserve"], "projected_balance_vials": m["projected_balance"],
             "to_order_vials": m["shortage"]} for drug, m in forecast(DB_NAME, target_date).items()]


# =========================
# ADD / EDIT / VIEW PATIENT (Паспортная часть)
# =========================
class PatientForm(QDialog):
    def __init__(self, p=None, readonly=False):
        super().__init__()
        self.p = p
        self.readonly = readonly
        self.setWindowTitle("Паспортная часть — просмотр" if readonly else "Паспортная часть")
        self.resize(900, 720)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(16)
        title = QLabel("Паспортная часть" if p else "Новый пациент")
        title.setProperty("role", "title")
        outer.addWidget(title)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        form = QWidget()
        form.setObjectName("workSheet")
        grid = QGridLayout(form)
        grid.setContentsMargins(20, 20, 20, 20)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
        for col in range(6):
            grid.setColumnStretch(col, 1)

        def field(label, widget, row, col, span=2):
            caption = QLabel(label)
            caption.setBuddy(widget)
            widget.setAccessibleName(label)
            box = QVBoxLayout()
            box.setSpacing(6)
            box.addWidget(caption)
            box.addWidget(widget)
            grid.addLayout(box, row, col, 1, span)

        self.last, self.first, self.middle = QLineEdit(), QLineEdit(), QLineEdit()
        field("Фамилия", self.last, 0, 0)
        field("Имя", self.first, 0, 2)
        field("Отчество", self.middle, 0, 4)
        self.birth = QLineEdit()
        self.birth.setPlaceholderText("ДД.ММ.ГГГГ")
        self.birth.textChanged.connect(self.format_date)
        self.gender = QComboBox()
        self.gender.addItems(["Мальчик", "Девочка"])
        self.mkb = QComboBox()
        self.mkb.addItems(MKB_CODES)
        field("Дата рождения", self.birth, 1, 0)
        field("Пол", self.gender, 1, 2)
        field("Диагноз МКБ-10", self.mkb, 1, 4)
        self.addr_reg, self.addr_fact = QLineEdit(), QLineEdit()
        field("Адрес регистрации", self.addr_reg, 2, 0, 3)
        field("Фактический адрес", self.addr_fact, 2, 3, 3)
        self.btn_copy = QPushButton("АР совпадает АФ")
        self.btn_copy.clicked.connect(lambda: self.addr_fact.setText(self.addr_reg.text()))
        grid.addWidget(self.btn_copy, 3, 3, 1, 3)
        self.clinic = QComboBox()
        self.clinic.addItems(CLINICS)
        self.uch = QLineEdit()
        field("Поликлиника", self.clinic, 4, 0, 3)
        field("Участок", self.uch, 4, 3, 3)
        self.doctor, self.phone = QLineEdit(), QLineEdit()
        self.phone.setPlaceholderText("+7(9XX)-XXX-XX-XX")
        self.phone.textChanged.connect(self.format_phone)
        field("Ведущий педиатр — ФИО", self.doctor, 5, 0, 3)
        field("Мобильный телефон", self.phone, 5, 3, 3)
        self.invalid = QCheckBox("Ребёнок инвалид")
        self.weight = QLineEdit()
        self.weight.setPlaceholderText("Например, 28,5")
        field("Вес, кг", self.weight, 6, 0, 3)
        grid.addWidget(self.invalid, 6, 3, 1, 3)
        grid.setRowStretch(7, 1)
        scroll.setWidget(form)
        outer.addWidget(scroll, 1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        if readonly:
            close_btn = QPushButton("Закрыть")
            close_btn.clicked.connect(self.accept)
            buttons.addWidget(close_btn)
        else:
            cancel = QPushButton("Отмена")
            cancel.clicked.connect(self.cancel)
            save = QPushButton("Сохранить")
            save.setProperty("role", "primary")
            save.clicked.connect(self.save)
            buttons.addWidget(cancel)
            buttons.addWidget(save)
        outer.addLayout(buttons)
        if p:
            self.load_data()
        if readonly:
            for widget in (self.last, self.first, self.middle, self.birth, self.gender,
                           self.mkb, self.addr_reg, self.addr_fact, self.btn_copy,
                           self.clinic, self.uch, self.doctor, self.phone, self.invalid, self.weight):
                widget.setEnabled(False)

    def format_date(self):
        t = self.birth.text().replace(".", "")
        if len(t) > 8:
            t = t[:8]
        if len(t) >= 4 and t.isdigit():
            self.birth.blockSignals(True)
            self.birth.setText(f"{t[:2]}.{t[2:4]}.{t[4:]}")
            self.birth.blockSignals(False)

    def format_phone(self):
        digits = re.sub(r"\D", "", self.phone.text())
        if len(digits) == 10 and digits.startswith("9"):
            digits = "7" + digits
        if len(digits) == 11 and digits[0] in "78":
            formatted = f"+7({digits[1:4]})-{digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
            self.phone.blockSignals(True)
            self.phone.setText(formatted)
            self.phone.blockSignals(False)

    def save(self):
        if self.readonly:
            return
        if not self.last.text().strip() or not self.first.text().strip():
            QMessageBox.warning(self, "Ошибка", "Заполните хотя бы фамилию и имя.")
            return
        try:
            birth = datetime.strptime(self.birth.text().strip(), "%d.%m.%Y")
            if birth.date() > datetime.now().date():
                raise ValueError()
        except ValueError:
            QMessageBox.warning(self, "Ошибка", "Укажите действительную дату рождения ДД.ММ.ГГГГ, не позднее сегодняшнего дня.")
            return
        self.format_phone()
        phone = self.phone.text().strip()
        if phone and not re.fullmatch(r"\+7\(9\d{2}\)-\d{3}-\d{2}-\d{2}", phone):
            QMessageBox.warning(self, "Ошибка", "Введите мобильный номер: +7(9XX)-XXX-XX-XX.")
            return
        weight_text = self.weight.text().strip().replace(",", ".")
        weight_val = None
        if weight_text:
            try:
                weight_val = float(weight_text)
                if not math.isfinite(weight_val) or weight_val <= 0:
                    raise ValueError()
            except ValueError:
                QMessageBox.warning(self, "Ошибка", "Вес должен быть положительным числом, например 28.5.")
                return
        if not confirm(self, "Вы уверены, что хотите сохранить?"):
            return
        data = (
            self.last.text().strip(), self.first.text().strip(), self.middle.text().strip(),
            birth.strftime("%d.%m.%Y"), self.mkb.currentText(), self.gender.currentText(),
            self.addr_reg.text(), self.addr_fact.text(), self.clinic.currentText(),
            self.uch.text(), self.doctor.text(), phone, int(self.invalid.isChecked()), weight_val
        )
        con = None
        try:
            con = sqlite3.connect(DB_NAME)
            with con:
                if self.p:
                    result = con.execute("""UPDATE patients SET
                        last=?,first=?,middle=?,birth=?,mkb=?,gender=?,address_reg=?,address_fact=?,
                        clinic=?,uch=?,doctor=?,phone=?,invalid=?,weight=? WHERE id=?""", (*data, self.p[0]))
                    if result.rowcount != 1:
                        raise ValueError("Пациент уже удалён. Обновите список.")
                    self.saved_id = self.p[0]
                else:
                    self.saved_id = con.execute("""INSERT INTO patients(
                        last,first,middle,birth,mkb,gender,address_reg,address_fact,
                        clinic,uch,doctor,phone,invalid,weight) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", data).lastrowid
        except (sqlite3.Error, ValueError) as exc:
            QMessageBox.warning(self, "Ошибка сохранения", str(exc))
            return
        finally:
            if con is not None:
                con.close()
        self.accept()

    def cancel(self):
        self.reject()

    def reject(self):
        if self.readonly or confirm(self, "Вы уверены, что хотите отменить?"):
            super().reject()

    def closeEvent(self, event):
        if self.readonly or confirm(self, "Вы уверены, что хотите отменить?"):
            event.accept()
        else:
            event.ignore()

    def load_data(self):
        p = tuple("" if value is None else value for value in self.p)
        for combo, value in [(self.mkb, p[5]), (self.gender, p[6]), (self.clinic, p[9])]:
            if value and combo.findText(value) == -1:
                combo.addItem(value)
        self.last.setText(p[1])
        self.first.setText(p[2])
        self.middle.setText(p[3])
        self.birth.setText(p[4])
        self.mkb.setCurrentText(p[5])
        self.gender.setCurrentText(p[6])
        self.addr_reg.setText(p[7])
        self.addr_fact.setText(p[8])
        self.clinic.setCurrentText(p[9])
        self.uch.setText(p[10])
        self.doctor.setText(p[11])
        self.phone.setText(p[12])
        self.invalid.setChecked(bool(p[13]))
        if len(p) > 14 and p[14] is not None:
            self.weight.setText(str(p[14]))


# =========================
# АНАЛИЗЫ
# =========================
class AnalysisWindow(QDialog):
    def __init__(self, pid, readonly=False):
        super().__init__()
        self.pid = pid
        self.readonly = readonly
        self.blocks = []
        self.setWindowTitle("Анализы — просмотр" if readonly else "Анализы")
        self.resize(900, 600)
        layout = QVBoxLayout(self)
        self.t = QTableWidget(0, 5)
        self.t.setHorizontalHeaderLabels(["Анализ", "Показатель", "Результат", "Ед. изм.", "Файл"])
        self.t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.t)
        buttons = QHBoxLayout()
        if not readonly:
            add = QPushButton("+")
            add.clicked.connect(self.on_add)
            buttons.addWidget(add)
            save = QPushButton("Сохранить")
            save.clicked.connect(self.save)
            buttons.addWidget(save)
        close = QPushButton("Закрыть" if readonly else "Отмена")
        close.clicked.connect(self.accept if readonly else self.cancel)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.load_existing()

    def load_existing(self):
        from storage import load_analyses
        try:
            rows = load_analyses(DB_NAME, self.pid)
        except (sqlite3.Error, ValueError) as exc:
            QMessageBox.warning(self, "Ошибка загрузки", str(exc))
            self.readonly = True
            return
        for record in rows:
            self.add_block(record["type"], record["data"], record["file"],
                           record["db_id"], malformed=record.get("malformed", False))

    def on_add(self):
        if not self.readonly:
            self.add_block("")

    def add_block(self, atype, values=None, file_path=None, db_id=None, malformed=False):
        values = dict(values or {})
        definitions = list(ANALYSIS_DEFS.get(atype, []))
        known = {label for label, _ in definitions}
        definitions.extend((str(key), "") for key in values if key not in known)
        if not definitions:
            definitions = [("Выберите тип анализа" if not atype else "Результат", "")]
        start = self.t.rowCount()
        self.t.setRowCount(start + len(definitions))
        block = {"type": atype, "start": start, "count": len(definitions), "file": file_path,
                 "db_id": db_id, "values": values, "malformed": malformed, "pending_file": None}
        self.blocks.append(block)
        idx = len(self.blocks) - 1
        for offset, (label, unit) in enumerate(definitions):
            row = start + offset
            for column, text in [(0, atype if offset == 0 else ""), (1, label),
                                 (2, str(values.get(label, ""))), (3, unit)]:
                item = QTableWidgetItem(text)
                if column != 2 or self.readonly or malformed or not atype or isinstance(values.get(label), (dict, list)):
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.t.setItem(row, column, item)
        if len(definitions) > 1:
            self.t.setSpan(start, 0, len(definitions), 1)
            self.t.setSpan(start, 4, len(definitions), 1)
        if not atype:
            choice = QComboBox()
            choice.addItem("Выберите анализ…")
            choice.addItems(ANALYSIS_DEFS)
            choice.currentTextChanged.connect(partial(self.select_type, idx))
            self.t.setCellWidget(start, 0, choice)
        elif malformed:
            self.t.item(start, 0).setText(f"{atype} (старые данные, только просмотр)")
        btn = QPushButton()
        block["btn"] = btn
        btn.clicked.connect(partial(self.handle_file, idx))
        self.t.setCellWidget(start, 4, btn)
        self.update_file_btn(idx)

    def block_values(self, block):
        values = dict(block["values"])
        for offset in range(block["count"]):
            row = block["start"] + offset
            label = self.t.item(row, 1).text()
            if not isinstance(values.get(label), (dict, list)):
                values[label] = self.t.item(row, 2).text()
        return values

    def select_type(self, idx, atype):
        if atype not in ANALYSIS_DEFS:
            return
        records = []
        for index, block in enumerate(self.blocks):
            records.append((atype if index == idx else block["type"],
                            {} if index == idx else self.block_values(block),
                            block["file"], block["db_id"], block["malformed"], block["pending_file"]))
        self.blocks = []
        self.t.clearSpans()
        self.t.setRowCount(0)
        for kind, values, path, rid, malformed, pending in records:
            self.add_block(kind, values, path, rid, malformed)
            self.blocks[-1]["pending_file"] = pending
            self.update_file_btn(len(self.blocks) - 1)

    def update_file_btn(self, idx):
        block = self.blocks[idx]
        path = block["pending_file"] or block["file"]
        block["btn"].setText(os.path.basename(path) if path else
                             ("Нет файла" if self.readonly else "Добавить файл"))
        block["btn"].setEnabled(bool(path) or (not self.readonly and not block["malformed"]))

    def file_path(self, path):
        if os.path.isabs(path):
            return path
        database_dir = os.path.dirname(os.path.abspath(DB_NAME))
        resolved = os.path.join(database_dir, path)
        if not os.path.isfile(resolved) and os.path.basename(database_dir).lower() == "dist":
            legacy = os.path.abspath(os.path.join(database_dir, os.pardir, path))
            legacy_files = os.path.abspath(os.path.join(database_dir, os.pardir, "files"))
            if os.path.commonpath([legacy, legacy_files]) == legacy_files and os.path.isfile(legacy):
                return legacy
        return resolved

    def open_file(self, path):
        resolved = self.file_path(path)
        if not os.path.isfile(resolved):
            QMessageBox.warning(self, "Файл", "Файл не найден на диске.")
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(resolved)):
            QMessageBox.warning(self, "Файл", "Не удалось открыть файл. Проверьте приложение для этого типа файлов.")

    def handle_file(self, idx):
        block = self.blocks[idx]
        path = block["pending_file"] or block["file"]
        if self.readonly or block["malformed"]:
            if path:
                self.open_file(path)
            return
        if path:
            box = QMessageBox(self)
            box.setWindowTitle("Файл анализа")
            box.setText("Файл уже прикреплён. Что сделать?")
            open_btn = box.addButton("Открыть", QMessageBox.ButtonRole.AcceptRole)
            replace_btn = box.addButton("Заменить", QMessageBox.ButtonRole.ActionRole)
            box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() == open_btn:
                self.open_file(path)
                return
            if box.clickedButton() != replace_btn:
                return
        chosen, _ = QFileDialog.getOpenFileName(self, "Выберите файл анализа")
        if chosen:
            block["pending_file"] = os.path.abspath(chosen)
            self.update_file_btn(idx)

    def save(self):
        if self.readonly:
            return
        if any(not block["type"] for block in self.blocks):
            QMessageBox.warning(self, "Анализы", "Выберите тип каждого добавленного анализа.")
            return
        if not confirm(self, "Вы уверены, что хотите сохранить?"):
            return
        copied = []
        records = []
        try:
            for block in self.blocks:
                if block["malformed"]:
                    continue  # Keep the original invalid JSON untouched in SQLite.
                path = block["file"]
                if block["pending_file"]:
                    os.makedirs(FILES_DIR, exist_ok=True)
                    name = uuid4().hex + "_" + os.path.basename(block["pending_file"])
                    destination = os.path.join(FILES_DIR, name)
                    copied.append(destination)
                    shutil.copy2(block["pending_file"], destination)
                    path = os.path.relpath(destination, os.path.dirname(os.path.abspath(DB_NAME)))
                records.append({"db_id": block["db_id"], "type": block["type"],
                                "data": self.block_values(block), "file": path})
            save_analyses(DB_NAME, self.pid, records)
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            for path in copied:
                try:
                    os.remove(path)
                except OSError:
                    pass
            QMessageBox.warning(self, "Ошибка сохранения", str(exc))
            return
        self.accept()

    def cancel(self):
        self.reject()

    def reject(self):
        if self.readonly or confirm(self, "Вы уверены, что хотите отменить?"):
            super().reject()

    def closeEvent(self, event):
        if self.readonly or confirm(self, "Вы уверены, что хотите отменить?"):
            event.accept()
        else:
            event.ignore()


# =========================
# КАРТОЧКА ПАЦИЕНТА (Редактировать / Просмотреть)
# =========================
class AddTherapyDialog(QDialog):
    """Диалог добавления одного назначения препарата с автоматическим расчётом дозы."""

    def __init__(self, pid, weight_kg, parent=None):
        super().__init__(parent)
        self.pid = pid
        self.weight_kg = weight_kg
        self.result_data = None
        self._calc_cache = None

        self.setWindowTitle("Добавить назначение препарата")
        self.resize(520, 480)

        l = QVBoxLayout()

        if not weight_kg:
            l.addWidget(QLabel(
                "<span style='color:red'>Внимание: у пациента не указан вес в паспортной части. "
                "Расчёт дозы недоступен, пока вес не заполнен.</span>"
            ))

        l.addWidget(QLabel(f"Вес пациента: <b>{weight_kg if weight_kg else 'не указан'} кг</b>"))

        l.addWidget(QLabel("Препарат"))
        self.drug_box = QComboBox()
        self.drug_box.addItems(list(DRUGS.keys()))
        self.drug_box.currentTextChanged.connect(self.rebuild)
        l.addWidget(self.drug_box)

        l.addWidget(QLabel("Диагноз"))
        self.diag_box = QComboBox()
        l.addWidget(self.diag_box)

        l.addWidget(QLabel("Этап терапии"))
        self.stage_box = QComboBox()
        self.stage_box.addItems(STAGES)
        self.stage_box.currentTextChanged.connect(self.rebuild)
        l.addWidget(self.stage_box)

        l.addWidget(QLabel("Схема / режим"))
        self.regimen_box = QComboBox()
        self.regimen_box.currentIndexChanged.connect(self.recalc)
        l.addWidget(self.regimen_box)

        l.addWidget(QLabel("Неделя введения (для индукции)"))
        self.week_box = QComboBox()
        self.week_box.currentIndexChanged.connect(self.recalc)
        l.addWidget(self.week_box)

        self.date_edit = QLineEdit(datetime.now().strftime("%d.%m.%Y"))
        l.addWidget(QLabel("Дата введения (ДД.ММ.ГГГГ)"))
        l.addWidget(self.date_edit)

        # блок результатов расчёта
        res_box = QGroupBox("Расчёт")
        res_l = QVBoxLayout()
        self.res_dose = QLabel("-")
        self.res_total = QLabel("-")
        self.res_vials = QLabel("-")
        self.res_waste = QLabel("-")
        for lbl in [self.res_dose, self.res_total, self.res_vials, self.res_waste]:
            res_l.addWidget(lbl)
        res_box.setLayout(res_l)
        l.addWidget(res_box)

        self.comment = QLineEdit()
        self.comment.setPlaceholderText("Комментарий (необязательно)")
        l.addWidget(self.comment)

        btn_row = QHBoxLayout()
        save_btn = QPushButton("Сохранить")
        cancel_btn = QPushButton("Отмена")
        cancel_btn.setProperty("role", "secondary")
        save_btn.clicked.connect(self.save)
        cancel_btn.clicked.connect(self.cancel)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)
        l.addLayout(btn_row)

        self.setLayout(l)
        self.diag_box.currentTextChanged.connect(self.recalc)
        self.rebuild()

    def current_drug_cfg(self):
        return DRUGS[self.drug_box.currentText()]

    def rebuild(self):
        cfg = self.current_drug_cfg()
        stage = self.stage_box.currentText()

        # диагноз (сохраняем текущий выбор при смене этапа, сбрасываем только при смене препарата)
        prev_diag = self.diag_box.currentText()
        self.diag_box.clear()
        if cfg.get("requires_diagnosis"):
            self.diag_box.addItems(cfg["diagnoses"])
            self.diag_box.setEnabled(True)
            if prev_diag in cfg["diagnoses"]:
                self.diag_box.setCurrentText(prev_diag)
        else:
            self.diag_box.addItem("—")
            self.diag_box.setEnabled(False)

        # схема/режим и неделя
        self.regimen_box.clear()
        self.week_box.clear()
        self.week_box.setEnabled(False)
        self.regimen_box.setEnabled(True)

        if stage == "Индукция":
            weeks = cfg["induction"]["weeks"]
            self.week_box.addItems([f"Неделя {w}" for w in weeks])
            self.week_box.setEnabled(True)
            self.regimen_box.addItem("Индукционное введение")
            self.regimen_box.setEnabled(False)
        elif stage == "Поддерживающая терапия":
            opts = cfg.get("maintenance_options") or []
            if not opts:
                self.regimen_box.addItem("Недоступно для этого препарата")
                self.regimen_box.setEnabled(False)
            else:
                for o in opts:
                    self.regimen_box.addItem(o["label"])
        else:  # Интенсификация
            opts = cfg.get("intensification_options")
            if not opts:
                self.regimen_box.addItem("Недоступно (в таблице указано «нет»)")
                self.regimen_box.setEnabled(False)
            else:
                for o in opts:
                    self.regimen_box.addItem(o["label"])

        self.recalc()

    def _week_dose_flat(self, cfg, week_index):
        """Доза индукции для Адалимумаба (плоское значение мг по диагнозу+весу+неделе)."""
        diag = self.diag_box.currentText()
        threshold = cfg["induction"]["weight_threshold"]
        band = ">40" if (self.weight_kg or 0) > threshold else "<40"
        doses = cfg["induction"]["dose_by_week"][diag][band]
        return doses[week_index]

    def recalc(self):
        self._calc_cache = None
        cfg = self.current_drug_cfg()
        stage = self.stage_box.currentText()
        vial_mg = cfg["vial_mg"]
        weight = self.weight_kg

        total_mg = None
        dose_label = "-"
        interval_weeks = None

        if not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight <= 0:
            self.res_dose.setText("Доза: укажите конечный положительный вес пациента")
            self.res_total.setText("Требуется, мг: -")
            self.res_vials.setText("Флаконов/шприцов: -")
            self.res_waste.setText("Остаток при введении, мг: -")
            return

        if stage == "Индукция":
            rule = cfg["induction"].get("dose_rule")
            if rule and rule[0] == "mg_per_kg":
                total_mg = rule[1] * weight
                dose_label = f"Доза: {rule[1]} мг/кг × {weight} кг"
            elif rule and rule[0] == "weight_band_mg_per_kg":
                band = self._pick_weight_band(rule[1], weight)
                if band:
                    total_mg = band["dose_mg_per_kg"] * weight
                    interval_weeks = band["interval_weeks"]
                    dose_label = f"Доза: {band['dose_mg_per_kg']} мг/кг × {weight} кг (по весовой группе)"
            elif "dose_by_week" in cfg["induction"]:
                if self.week_box.currentIndex() >= 0 and self.diag_box.currentText():
                    try:
                        total_mg = self._week_dose_flat(cfg, self.week_box.currentIndex())
                        dose_label = f"Доза на этой неделе: {total_mg} мг (фикс. по диагнозу/весу)"
                    except (KeyError, IndexError, TypeError):
                        total_mg = None

        else:  # Поддерживающая терапия / Интенсификация
            opts = cfg.get("maintenance_options") if stage == "Поддерживающая терапия" else cfg.get("intensification_options")
            idx = self.regimen_box.currentIndex()
            if opts and 0 <= idx < len(opts):
                opt = opts[idx]
                if "dose_rule" in opt:
                    rule = opt["dose_rule"]
                    if rule[0] == "mg_per_kg":
                        total_mg = rule[1] * weight
                        dose_label = f"Доза: {rule[1]} мг/кг × {weight} кг"
                        interval_weeks = opt.get("interval_weeks")
                    elif rule[0] == "weight_band_mg_per_kg":
                        band = self._pick_weight_band(rule[1], weight)
                        if band:
                            total_mg = band["dose_mg_per_kg"] * weight
                            interval_weeks = band["interval_weeks"]
                            dose_label = f"Доза: {band['dose_mg_per_kg']} мг/кг × {weight} кг (по весовой группе)"
                elif "dose_flat" in opt:
                    diag = self.diag_box.currentText()
                    threshold = 40
                    band = ">40" if weight > threshold else "<40"
                    flat = opt["dose_flat"].get(diag, {}).get(band)
                    interval_weeks = opt.get("interval_weeks")
                    if flat is None:
                        dose_label = ("Доза для этой комбинации диагноз/вес не указана в исходной таблице. "
                                      "Уточните дозу вручную в комментарии.")
                        total_mg = None
                    else:
                        total_mg = flat
                        dose_label = f"Доза: {flat} мг (фикс. по диагнозу/весу)"

        self.res_dose.setText(dose_label)

        if total_mg:
            vials, administered, waste = calc_vials(total_mg, vial_mg)
            unit_name = cfg.get("vial_unit_name", "флакон")
            self.res_total.setText(f"Требуется, мг: {round(total_mg, 2)}")
            self.res_vials.setText(f"Нужно {unit_name}(-ов): {vials} × {vial_mg} мг = {administered} мг")
            self.res_waste.setText(f"Остаток (списание), мг: {waste}")
            if interval_weeks:
                self.res_waste.setText(self.res_waste.text() + f" | Интервал: 1 раз в {interval_weeks} нед.")
            self._calc_cache = {
                "total_mg": total_mg, "vials": vials,
                "administered": administered, "waste": waste,
                "interval_weeks": interval_weeks,
            }
        else:
            self.res_total.setText("Требуется, мг: -")
            self.res_vials.setText("Флаконов/шприцов: -")
            self.res_waste.setText("Остаток при введении, мг: -")
            self._calc_cache = None

    @staticmethod
    def _pick_weight_band(bands, weight):
        for b in bands:
            if "min_kg" in b and weight >= b["min_kg"]:
                return b
        for b in bands:
            if "max_kg" in b and weight < b["max_kg"]:
                return b
        return bands[0] if bands else None

    def save(self):
        try:
            date_text = self.date_edit.text().strip()
            if not re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{4}", date_text):
                raise ValueError()
            administration_date = datetime.strptime(date_text, "%d.%m.%Y").strftime("%d.%m.%Y")
        except ValueError:
            QMessageBox.warning(self, "Ошибка", "Укажите действительную дату введения ДД.ММ.ГГГГ.")
            return
        if not self._calc_cache:
            QMessageBox.warning(self, "Ошибка", "Расчёт дозы не выполнен — проверьте вес пациента и выбранные параметры.")
            return
        if not confirm(self, "Вы уверены, что хотите сохранить это назначение?"):
            return

        cfg = self.current_drug_cfg()
        stage = self.stage_box.currentText()
        regimen_label = self.week_box.currentText() if stage == "Индукция" else self.regimen_box.currentText()

        self.result_data = {
            "pid": self.pid,
            "date": administration_date,
            "drug": self.drug_box.currentText(),
            "diagnosis": self.diag_box.currentText() if cfg.get("requires_diagnosis") else None,
            "stage": stage,
            "regimen_label": regimen_label,
            "weight_kg": self.weight_kg,
            "total_mg_needed": self._calc_cache["total_mg"],
            "vial_mg": cfg["vial_mg"],
            "vials_count": self._calc_cache["vials"],
            "total_mg_administered": self._calc_cache["administered"],
            "waste_mg": self._calc_cache["waste"],
            "interval_weeks": self._calc_cache["interval_weeks"],
            "comment": self.comment.text(),
        }
        self.accept()

    def cancel(self):
        if confirm(self, "Вы уверены, что хотите отменить?"):
            self.reject()


class TherapyWindow(QDialog):
    """Список назначений препаратов для пациента + добавление новых."""

    def __init__(self, pid, readonly=False):
        super().__init__()
        self.pid = pid
        self.readonly = readonly
        self.setWindowTitle("Препараты — просмотр" if readonly else "Препараты")
        self.resize(800, 450)

        l = QVBoxLayout()

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT weight FROM patients WHERE id=?", (pid,))
        row = cur.fetchone()
        con.close()
        self.weight_kg = row[0] if row else None

        self.t = QTableWidget()
        self.t.setColumnCount(8)
        self.t.setHorizontalHeaderLabels(
            ["Дата", "Препарат", "Диагноз", "Этап", "Схема", "Доза, мг", "Флаконов/шприцов", "Остаток, мг"]
        )
        self.t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        l.addWidget(self.t)

        btn_row = QHBoxLayout()
        if not readonly:
            add_btn = QPushButton("+ Добавить назначение")
            add_btn.clicked.connect(self.on_add)
            del_btn = QPushButton("Удалить запись")
            del_btn.setProperty("role", "danger")
            del_btn.clicked.connect(self.on_delete)
            btn_row.addWidget(add_btn)
            btn_row.addWidget(del_btn)
        btn_row.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.setProperty("role", "secondary")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        l.addLayout(btn_row)

        self.setLayout(l)
        self.load()

    def load(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("""SELECT id,date,drug,diagnosis,stage,regimen_label,
                       total_mg_needed,vials_count,waste_mg
                       FROM therapy WHERE pid=? ORDER BY id DESC""", (self.pid,))
        self.rows = cur.fetchall()
        con.close()

        self.t.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            _, date, drug, diagnosis, stage, regimen, total_mg, vials, waste = row
            values = [date, drug, diagnosis or "-", stage, regimen,
                      str(round(total_mg, 2)) if total_mg else "-",
                      str(vials) if vials else "-",
                      str(waste) if waste is not None else "-"]
            for c, v in enumerate(values):
                self.t.setItem(r, c, QTableWidgetItem(v))

    def on_add(self):
        if not self.weight_kg:
            QMessageBox.warning(
                self, "Нет веса",
                "У пациента не указан вес в паспортной части. Заполните вес, чтобы рассчитать дозу."
            )
        dlg = AddTherapyDialog(self.pid, self.weight_kg, parent=self)
        if dlg.exec() and dlg.result_data:
            d = dlg.result_data
            con = sqlite3.connect(DB_NAME)
            cur = con.cursor()
            cur.execute("""
                INSERT INTO therapy(
                    pid,date,drug,diagnosis,stage,regimen_label,weight_kg,
                    dose_value,dose_unit,interval_weeks,total_mg_needed,vial_mg,
                    vials_count,total_mg_administered,waste_mg,comment
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                d["pid"], d["date"], d["drug"], d["diagnosis"], d["stage"], d["regimen_label"],
                d["weight_kg"], d["total_mg_needed"], "мг", d["interval_weeks"], d["total_mg_needed"],
                d["vial_mg"], d["vials_count"], d["total_mg_administered"], d["waste_mg"], d["comment"]
            ))
            con.commit()
            con.close()
            self.load()

    def on_delete(self):
        r = self.t.currentRow()
        if r == -1:
            return
        if not confirm(self, "Удалить выбранную запись о назначении?"):
            return
        rid = self.rows[r][0]
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("DELETE FROM therapy WHERE id=?", (rid,))
        con.commit()
        con.close()
        self.load()


class AddStockDialog(QDialog):
    """Небольшой диалог добавления прихода препарата на склад."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Приход препарата")
        self.resize(360, 240)
        self.result_data = None

        l = QVBoxLayout()

        l.addWidget(QLabel("Препарат"))
        self.drug_box = QComboBox()
        self.drug_box.addItems(list(DRUGS.keys()))
        l.addWidget(self.drug_box)

        l.addWidget(QLabel("Дата поступления (ДД.ММ.ГГГГ)"))
        self.date_edit = QLineEdit(datetime.now().strftime("%d.%m.%Y"))
        l.addWidget(self.date_edit)

        l.addWidget(QLabel("Количество флаконов/шприцов"))
        self.qty_edit = QLineEdit()
        self.qty_edit.setPlaceholderText("например 20")
        l.addWidget(self.qty_edit)

        l.addWidget(QLabel("Комментарий (партия, поставщик и т.п.)"))
        self.comment_edit = QLineEdit()
        l.addWidget(self.comment_edit)

        btn_row = QHBoxLayout()
        save_btn = QPushButton("Сохранить")
        cancel_btn = QPushButton("Отмена")
        cancel_btn.setProperty("role", "secondary")
        save_btn.clicked.connect(self.save)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)
        l.addLayout(btn_row)

        self.setLayout(l)

    def save(self):
        date_val = parse_date(self.date_edit.text())
        if not date_val:
            QMessageBox.warning(self, "Ошибка", "Введите дату в формате ДД.ММ.ГГГГ")
            return
        try:
            qty = int(self.qty_edit.text().strip())
            if qty <= 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Ошибка", "Количество должно быть целым положительным числом")
            return
        if not confirm(self, "Сохранить приход препарата на склад?"):
            return

        drug = self.drug_box.currentText()
        self.result_data = {
            "date": self.date_edit.text(),
            "drug": drug,
            "vials_count": qty,
            "vial_mg": DRUGS[drug]["vial_mg"],
            "comment": self.comment_edit.text(),
        }
        self.accept()


class ReportsWindow(QDialog):
    """Окно отчётов: приход на склад, текущий остаток, прогноз потребности на дату."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Отчёты по препаратам")
        self.resize(820, 520)

        l = QVBoxLayout()
        title = QLabel("Отчёты по препаратам")
        title.setProperty("role", "title")
        l.addWidget(title)

        self.tabs = QTabWidget()
        l.addWidget(self.tabs)

        self.tabs.addTab(self.build_receipts_tab(), "Приход")
        self.tabs.addTab(self.build_stock_tab(), "Текущий остаток")
        self.tabs.addTab(self.build_forecast_tab(), "Прогноз на дату")

        close_btn = QPushButton("Закрыть")
        close_btn.setProperty("role", "secondary")
        close_btn.clicked.connect(self.accept)
        l.addWidget(close_btn)

        self.setLayout(l)

        self.load_receipts()
        self.load_stock()

    # ---------- Приход ----------
    def build_receipts_tab(self):
        w = QWidget()
        l = QVBoxLayout()

        self.receipts_table = QTableWidget()
        self.receipts_table.setColumnCount(4)
        self.receipts_table.setHorizontalHeaderLabels(["Дата", "Препарат", "Кол-во, флаконов", "Комментарий"])
        self.receipts_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.receipts_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        l.addWidget(self.receipts_table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Добавить приход")
        add_btn.clicked.connect(self.on_add_receipt)
        btn_row.addWidget(add_btn)
        btn_row.addStretch()
        l.addLayout(btn_row)

        w.setLayout(l)
        return w

    def load_receipts(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT date, drug, vials_count, comment FROM stock_receipts ORDER BY id DESC")
        rows = cur.fetchall()
        con.close()

        self.receipts_table.setRowCount(len(rows))
        for r, (date, drug, qty, comment) in enumerate(rows):
            for c, v in enumerate([date, drug, str(qty), comment or ""]):
                self.receipts_table.setItem(r, c, QTableWidgetItem(v))

    def on_add_receipt(self):
        dlg = AddStockDialog(self)
        if dlg.exec() and dlg.result_data:
            d = dlg.result_data
            con = sqlite3.connect(DB_NAME)
            cur = con.cursor()
            cur.execute(
                "INSERT INTO stock_receipts(date, drug, vials_count, vial_mg, comment) VALUES(?,?,?,?,?)",
                (d["date"], d["drug"], d["vials_count"], d["vial_mg"], d["comment"])
            )
            con.commit()
            con.close()
            self.load_receipts()
            self.load_stock()

    # ---------- Текущий остаток ----------
    def build_stock_tab(self):
        w = QWidget()
        l = QVBoxLayout()

        self.stock_table = QTableWidget()
        self.stock_table.setColumnCount(6)
        self.stock_table.setHorizontalHeaderLabels(
            ["Препарат", "Получено, фл.", "Списано, фл.", "Остаток, фл.", "Остаток, мг", "Флакон, мг"]
        )
        self.stock_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.stock_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        l.addWidget(self.stock_table)

        refresh_btn = QPushButton("Обновить")
        refresh_btn.setProperty("role", "secondary")
        refresh_btn.clicked.connect(self.load_stock)
        l.addWidget(refresh_btn)

        w.setLayout(l)
        return w

    def load_stock(self):
        summary = get_stock_summary()
        self.stock_table.setRowCount(len(summary))
        for r, s in enumerate(summary):
            values = [s["drug"], str(s["received_vials"]), str(s["used_vials"]),
                      str(s["balance_vials"]), str(round(s["balance_mg"], 1)), str(s["vial_mg"])]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                if c == 3 and s["balance_vials"] < 0:
                    item.setForeground(Qt.GlobalColor.red)
                self.stock_table.setItem(r, c, item)

    # ---------- Прогноз на дату ----------
    def build_forecast_tab(self):
        w = QWidget()
        l = QVBoxLayout()

        row = QHBoxLayout()
        row.addWidget(QLabel("Рассчитать потребность до даты:"))
        self.forecast_date = QLineEdit(datetime.now().strftime("%d.%m.%Y"))
        row.addWidget(self.forecast_date)
        calc_btn = QPushButton("Рассчитать")
        calc_btn.clicked.connect(self.on_calc_forecast)
        row.addWidget(calc_btn)
        l.addLayout(row)

        note = QLabel(
            "Прогноз строится по последнему сохранённому назначению «Поддерживающая терапия»/"
            "«Интенсификация» для каждого пациента: считается, сколько введений препарата "
            "выпадает на период до выбранной даты при текущем интервале, и сколько флаконов "
            "для этого потребуется."
        )
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        l.addWidget(note)

        self.forecast_table = QTableWidget()
        self.forecast_table.setColumnCount(5)
        self.forecast_table.setHorizontalHeaderLabels(
            ["Препарат", "Остаток сейчас, фл.", "Нужно до даты, фл.", "Останется, фл.", "Докупить, фл."]
        )
        self.forecast_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.forecast_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        l.addWidget(self.forecast_table)

        w.setLayout(l)
        return w

    def on_calc_forecast(self):
        target = parse_date(self.forecast_date.text())
        if not target:
            QMessageBox.warning(self, "Ошибка", "Введите дату в формате ДД.ММ.ГГГГ")
            return
        data = get_forecast(target)
        self.forecast_table.setRowCount(len(data))
        for r, d in enumerate(data):
            values = [d["drug"], str(d["current_balance_vials"]), str(d["needed_vials_by_date"]),
                      str(d["projected_balance_vials"]), str(d["to_order_vials"])]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                if c == 3 and d["projected_balance_vials"] < 0:
                    item.setForeground(Qt.GlobalColor.red)
                if c == 4 and d["to_order_vials"] > 0:
                    item.setForeground(Qt.GlobalColor.red)
                self.forecast_table.setItem(r, c, item)


class PatientCard(QDialog):
    def __init__(self, pid, mode="view"):
        super().__init__()
        self.pid = pid
        self.mode = mode  # 'view' or 'edit'
        self.setWindowTitle("Просмотр пациента" if mode == "view" else "Редактирование пациента")
        self.resize(760, 380)

        self.layout_main = QVBoxLayout()
        self.layout_main.setContentsMargins(24, 24, 24, 24)
        self.layout_main.setSpacing(20)
        self.info_box = QVBoxLayout()
        self.layout_main.addLayout(self.info_box)

        btn_row = QHBoxLayout()
        self.passport_btn = QPushButton("Паспортная часть")
        self.analyses_btn = QPushButton("Анализы")
        self.therapy_btn = QPushButton("План терапии")
        self.therapy_btn.setProperty("role", "primary")
        self.fed_btn = QPushButton("Федеральный центр")
        self.fed_btn.setProperty("role", "secondary")

        self.passport_btn.clicked.connect(self.open_passport)
        self.analyses_btn.clicked.connect(self.open_analyses)
        self.therapy_btn.clicked.connect(self.open_therapy)
        self.fed_btn.clicked.connect(
            lambda: QMessageBox.information(self, "Федеральный центр", "Раздел в разработке.")
        )

        btn_row.addWidget(self.passport_btn)
        btn_row.addWidget(self.analyses_btn)
        btn_row.addWidget(self.therapy_btn)
        btn_row.addWidget(self.fed_btn)
        self.layout_main.addLayout(btn_row)

        close_btn = QPushButton("Закрыть")
        close_btn.setProperty("role", "secondary")
        close_btn.clicked.connect(self.accept)
        self.layout_main.addWidget(close_btn)

        self.setLayout(self.layout_main)
        self.refresh_patient()

    def refresh_patient(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute(f"SELECT {PATIENT_COLUMNS} FROM patients WHERE id=?", (self.pid,))
        self.p = cur.fetchone()
        con.close()

        # очистить старые лейблы
        while self.info_box.count():
            item = self.info_box.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if not self.p:
            self.info_box.addWidget(QLabel("Пациент не найден"))
            return

        p = self.p
        fio = f"{p[1]} {p[2]} {p[3]}"
        self.info_box.addWidget(QLabel(f"<b>ФИО:</b> {fio}"))
        self.info_box.addWidget(QLabel(f"<b>Дата рождения:</b> {p[4]}"))
        self.info_box.addWidget(QLabel(f"<b>МКБ-10:</b> {p[5]}"))
        self.info_box.addWidget(QLabel(f"<b>Пол:</b> {p[6]}"))

    def open_passport(self):
        readonly = self.mode == "view"
        form = PatientForm(self.p, readonly=readonly)
        if form.exec() and not readonly:
            self.refresh_patient()

    def open_analyses(self):
        readonly = self.mode == "view"
        win = AnalysisWindow(self.pid, readonly=readonly)
        if win.exec() == QDialog.DialogCode.Accepted and not readonly:
            self.accept()

    def open_therapy(self):
        from medication_ui import TherapyPlanner
        TherapyPlanner(DB_NAME, self.pid, readonly=self.mode == "view", parent=self).exec()
        self.refresh_patient()


# =========================
# MAIN
# =========================
class App(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("applicationFrame")
        self.setWindowTitle("МедРегистр")
        self.resize(1280, 800)
        self.setMinimumSize(900, 620)
        frame = QHBoxLayout(self)
        frame.setContentsMargins(16, 16, 16, 16)
        frame.setSpacing(16)
        nav_widget = QWidget()
        nav_widget.setFixedWidth(196)
        nav = QVBoxLayout(nav_widget)
        nav.setContentsMargins(0, 8, 0, 8)
        nav.setSpacing(12)
        brand = QLabel("МедРегистр")
        brand.setProperty("role", "brand")
        nav.addWidget(brand)
        scope = QLabel("Детская\nгастроэнтерология")
        scope.setProperty("role", "muted")
        nav.addWidget(scope)
        nav.addSpacing(28)
        patients = QPushButton("Пациенты")
        patients.setProperty("role", "nav")
        patients.setCheckable(True)
        patients.setChecked(True)
        patients.clicked.connect(lambda: (patients.setChecked(True), self.load()))
        nav.addWidget(patients)
        pharmacy = QPushButton("Аптека и прогноз")
        pharmacy.setProperty("role", "nav")
        pharmacy.clicked.connect(self.open_reports)
        nav.addWidget(pharmacy)
        nav.addStretch()
        hint = QLabel("Планируйте введения.\nКонтролируйте остатки.")
        hint.setProperty("role", "muted")
        nav.addWidget(hint)
        frame.addWidget(nav_widget)
        sheet = QFrame()
        sheet.setObjectName("workSheet")
        content = QVBoxLayout(sheet)
        content.setContentsMargins(24, 24, 24, 24)
        content.setSpacing(16)
        header = QHBoxLayout()
        title = QLabel("Пациенты")
        title.setProperty("role", "title")
        header.addWidget(title)
        header.addStretch()
        add = QPushButton("Добавить пациента")
        add.setProperty("role", "primary")
        add.clicked.connect(self.add)
        header.addWidget(add)
        content.addLayout(header)
        sub = QLabel("Паспортная часть, анализы и календарь терапии")
        sub.setProperty("role", "muted")
        content.addWidget(sub)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Поиск по ФИО или диагнозу МКБ-10")
        self.search_box.setAccessibleName("Поиск пациентов")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self.apply_filter)
        content.addWidget(self.search_box)
        self.t = QTableWidget()
        self.t.setColumnCount(4)
        self.t.setHorizontalHeaderLabels(["ФИО", "Дата рождения", "МКБ-10", "Пол"])
        self.t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.t.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.t.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.t.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.t.verticalHeader().hide()
        self.t.verticalHeader().setDefaultSectionSize(48)
        self.t.setShowGrid(False)
        self.t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.t.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.t.doubleClicked.connect(self.view)
        content.addWidget(self.t, 1)
        self.status_label = QLabel()
        self.status_label.setProperty("role", "muted")
        content.addWidget(self.status_label)
        actions = QHBoxLayout()
        view = QPushButton("Просмотреть")
        view.clicked.connect(self.view)
        edit = QPushButton("Редактировать")
        edit.clicked.connect(self.edit)
        self.therapy_button = QPushButton("План терапии")
        self.therapy_button.clicked.connect(self.open_planner)
        actions.addWidget(view)
        actions.addWidget(edit)
        actions.addStretch()
        actions.addWidget(self.therapy_button)
        content.addLayout(actions)
        frame.addWidget(sheet, 1)
        self.data = []
        self.filtered = []
        self.load()

    def load(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute(f"SELECT {PATIENT_COLUMNS} FROM patients")
        self.data = cur.fetchall()
        con.close()
        self.apply_filter()

    def apply_filter(self):
        query = self.search_box.text().strip().lower()
        if not query:
            self.filtered = list(self.data)
        else:
            self.filtered = []
            for p in self.data:
                fio = f"{p[1]} {p[2]} {p[3]}".lower()
                mkb = (p[5] or "").lower()
                if query in fio or query in mkb:
                    self.filtered.append(p)

        self.t.setRowCount(len(self.filtered))
        for r, p in enumerate(self.filtered):
            fio = f"{p[1]} {p[2]} {p[3]}"
            self.t.setItem(r, 0, QTableWidgetItem(fio))
            self.t.setItem(r, 1, QTableWidgetItem(p[4]))
            self.t.setItem(r, 2, QTableWidgetItem(p[5]))
            self.t.setItem(r, 3, QTableWidgetItem(p[6]))

        total = len(self.data)
        shown = len(self.filtered)
        self.status_label.setText(
            f"Показано {shown} из {total}" if query else f"Всего пациентов: {total}"
        )

    def get(self):
        r = self.t.currentRow()
        if r == -1 or r >= len(self.filtered):
            return None
        return self.filtered[r]

    def add(self):
        if PatientForm().exec():
            self.load()

    def edit(self):
        p = self.get()
        if not p:
            QMessageBox.information(self, "Внимание", "Выберите пациента в таблице.")
            return
        card = PatientCard(p[0], mode="edit")
        card.exec()
        self.load()

    def view(self):
        p = self.get()
        if not p:
            QMessageBox.information(self, "Внимание", "Выберите пациента в таблице.")
            return
        card = PatientCard(p[0], mode="view")
        card.exec()
        self.load()

    def open_reports(self):
        from medication_ui import PharmacyWindow
        PharmacyWindow(DB_NAME, parent=self).exec()
        self.load()

    def open_planner(self):
        p = self.get()
        if not p:
            QMessageBox.information(self, "Пациент", "Выберите пациента в таблице.")
            return
        from medication_ui import TherapyPlanner
        TherapyPlanner(DB_NAME, p[0], parent=self).exec()
        self.load()


# =========================
# RUN
# =========================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    configure_app(app)
    try:
        init_db()
        w = App()
    except (sqlite3.Error, OSError, ValueError) as exc:
        QMessageBox.critical(None, "Ошибка открытия базы данных", str(exc))
        sys.exit(1)
    w.show()
    sys.exit(app.exec())
