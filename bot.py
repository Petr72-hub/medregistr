# UPDATED MEDICAL SYSTEM (MedRegister)
import sys, os, shutil, sqlite3, re, json, math
from datetime import datetime
from functools import partial
from PyQt6.QtWidgets import *
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices

DB_NAME = "children.db"
FILES_DIR = "files"
if not os.path.exists(FILES_DIR):
    os.makedirs(FILES_DIR)

MKB_CODES = [
    "K29 Гастрит", "K21 ГЭРБ", "K52 Гастроэнтерит", "K25 Язва желудка",
    "K26 Язва 12ПК", "K58 СРК", "E11 Диабет", "B15 Гепатит A"
]

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
STYLE_SHEET = """
QWidget {
    background-color: #F4F7FB;
    color: #1F2937;
    font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    font-size: 14px;
}
QDialog, QMainWindow { background-color: #F4F7FB; }

QLabel { color: #1F2937; }
QLabel[role="title"] { font-size: 18px; font-weight: 600; color: #0F172A; }
QLabel[role="muted"] { color: #64748B; font-size: 12px; }

QGroupBox {
    border: 1px solid #DCE3ED;
    border-radius: 10px;
    margin-top: 12px;
    padding: 10px;
    background-color: #FFFFFF;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: #2563EB;
}

QLineEdit, QComboBox, QDateEdit {
    background-color: #FFFFFF;
    border: 1px solid #D7DEE9;
    border-radius: 8px;
    padding: 6px 10px;
    min-height: 22px;
}
QLineEdit:focus, QComboBox:focus {
    border: 1px solid #2563EB;
}
QComboBox::drop-down { border: none; width: 22px; }

QPushButton {
    background-color: #2563EB;
    color: white;
    border: none;
    border-radius: 8px;
    padding: 8px 16px;
    font-weight: 600;
}
QPushButton:hover { background-color: #1D4ED8; }
QPushButton:pressed { background-color: #1E40AF; }
QPushButton:disabled { background-color: #B9C4D6; color: #EEF2F7; }

QPushButton[role="secondary"] {
    background-color: #E7ECF5;
    color: #1F2937;
}
QPushButton[role="secondary"]:hover { background-color: #D9E1EF; }

QPushButton[role="danger"] {
    background-color: #EF4444;
}
QPushButton[role="danger"]:hover { background-color: #DC2626; }

QPushButton[role="accent"] {
    background-color: #10B981;
}
QPushButton[role="accent"]:hover { background-color: #059669; }

QTableWidget {
    background-color: #FFFFFF;
    alternate-background-color: #F7F9FC;
    gridline-color: #E5EAF2;
    border: 1px solid #DCE3ED;
    border-radius: 10px;
    selection-background-color: #DBEAFE;
    selection-color: #0F172A;
}
QHeaderView::section {
    background-color: #EEF2F9;
    color: #334155;
    padding: 8px;
    border: none;
    border-bottom: 2px solid #DCE3ED;
    font-weight: 600;
}
QTableWidget::item { padding: 4px; }

QTabWidget::pane {
    border: 1px solid #DCE3ED;
    border-radius: 10px;
    background-color: #FFFFFF;
    top: -1px;
}
QTabBar::tab {
    background: #E7ECF5;
    color: #334155;
    padding: 8px 18px;
    margin-right: 4px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    font-weight: 600;
}
QTabBar::tab:selected {
    background: #2563EB;
    color: white;
}

QCheckBox { spacing: 8px; }
QScrollBar:vertical { width: 10px; background: transparent; }
QScrollBar::handle:vertical { background: #C7D0DE; border-radius: 5px; }
"""


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
    """Прогноз потребности в препаратах до заданной даты (datetime), на основе
    последних сохранённых назначений "Поддерживающая терапия"/"Интенсификация"
    у каждого пациента и их интервала между введениями."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("""
        SELECT pid, drug, date, interval_weeks, vials_count
        FROM therapy
        WHERE stage IN ('Поддерживающая терапия', 'Интенсификация')
          AND interval_weeks IS NOT NULL
        ORDER BY id
    """)
    rows = cur.fetchall()
    con.close()

    # оставляем только самое последнее (по дате) назначение на каждую пару пациент+препарат
    latest = {}
    for pid, drug, date_str, interval_weeks, vials_count in rows:
        d = parse_date(date_str)
        if not d:
            continue
        key = (pid, drug)
        if key not in latest or d > latest[key]["date"]:
            latest[key] = {"date": d, "interval_weeks": interval_weeks, "vials_count": vials_count}

    needed_vials = {drug: 0 for drug in DRUGS}
    for (pid, drug), info in latest.items():
        if drug not in DRUGS:
            continue
        interval_days = (info["interval_weeks"] or 0) * 7
        if interval_days <= 0:
            continue
        delta_days = (target_date - info["date"]).days
        if delta_days <= 0:
            continue
        doses_count = int(delta_days // interval_days)
        needed_vials[drug] += doses_count * (info["vials_count"] or 0)

    stock = {s["drug"]: s for s in get_stock_summary()}

    result = []
    for drug in DRUGS:
        current_balance = stock[drug]["balance_vials"] if drug in stock else 0
        need = needed_vials.get(drug, 0)
        after = current_balance - need
        to_order = max(0, -after)
        result.append({
            "drug": drug,
            "current_balance_vials": current_balance,
            "needed_vials_by_date": need,
            "projected_balance_vials": after,
            "to_order_vials": to_order,
        })
    return result


# =========================
# ADD / EDIT / VIEW PATIENT (Паспортная часть)
# =========================
class PatientForm(QDialog):
    def __init__(self, p=None, readonly=False):
        super().__init__()
        self.p = p
        self.readonly = readonly
        self.setWindowTitle("Пациент — просмотр" if readonly else "Пациент")
        self.resize(600, 750)

        l = QVBoxLayout()

        self.last = QLineEdit()
        self.last.setPlaceholderText("Фамилия")
        self.first = QLineEdit()
        self.first.setPlaceholderText("Имя")
        self.middle = QLineEdit()
        self.middle.setPlaceholderText("Отчество")

        l.addWidget(QLabel("Фамилия"))
        l.addWidget(self.last)
        l.addWidget(QLabel("Имя"))
        l.addWidget(self.first)
        l.addWidget(QLabel("Отчество"))
        l.addWidget(self.middle)

        self.birth = QLineEdit()
        self.birth.setPlaceholderText("ДД.ММ.ГГГГ")
        self.birth.textChanged.connect(self.format_date)
        l.addWidget(QLabel("Дата рождения"))
        l.addWidget(self.birth)

        self.gender = QComboBox()
        self.gender.addItems(["Мальчик", "Девочка"])
        l.addWidget(QLabel("Пол"))
        l.addWidget(self.gender)

        self.mkb = QComboBox()
        self.mkb.addItems(MKB_CODES)
        l.addWidget(QLabel("Диагноз МКБ-10"))
        l.addWidget(self.mkb)

        self.addr_reg = QLineEdit()
        self.addr_reg.setPlaceholderText("Адрес регистрации")
        l.addWidget(QLabel("Адрес регистрации"))
        l.addWidget(self.addr_reg)

        self.addr_fact = QLineEdit()
        self.addr_fact.setPlaceholderText("Адрес фактический")
        l.addWidget(QLabel("Адрес фактический"))
        l.addWidget(self.addr_fact)

        self.btn_copy = QPushButton("АР совпадает АФ")
        self.btn_copy.clicked.connect(lambda: self.addr_fact.setText(self.addr_reg.text()))
        l.addWidget(self.btn_copy)

        self.clinic = QComboBox()
        self.clinic.addItems(CLINICS)
        l.addWidget(QLabel("Поликлиника"))
        l.addWidget(self.clinic)

        self.uch = QLineEdit()
        self.uch.setPlaceholderText("Участок")
        l.addWidget(QLabel("Участок"))
        l.addWidget(self.uch)

        self.doctor = QLineEdit()
        self.doctor.setPlaceholderText("ФИО ведущего педиатра")
        l.addWidget(QLabel("Ведущий педиатр"))
        l.addWidget(self.doctor)

        self.phone = QLineEdit()
        self.phone.setPlaceholderText("+7(9XX)-XXX-XX-XX")
        self.phone.textChanged.connect(self.format_phone)
        l.addWidget(QLabel("Номер телефона"))
        l.addWidget(self.phone)

        self.invalid = QCheckBox("Ребёнок инвалид")
        l.addWidget(self.invalid)

        self.weight = QLineEdit()
        self.weight.setPlaceholderText("Вес, кг (например 28.5)")
        l.addWidget(QLabel("Вес пациента, кг (для расчёта доз препаратов)"))
        l.addWidget(self.weight)

        btn_row = QHBoxLayout()
        if readonly:
            close_btn = QPushButton("Закрыть")
            close_btn.setProperty("role", "secondary")
            close_btn.clicked.connect(self.accept)
            btn_row.addWidget(close_btn)
        else:
            save = QPushButton("Сохранить")
            cancel = QPushButton("Отмена")
            cancel.setProperty("role", "secondary")
            save.clicked.connect(self.save)
            cancel.clicked.connect(self.cancel)
            btn_row.addWidget(save)
            btn_row.addWidget(cancel)
        l.addLayout(btn_row)

        self.setLayout(l)

        if p:
            self.load_data()

        if readonly:
            for w in [self.last, self.first, self.middle, self.birth, self.gender,
                      self.mkb, self.addr_reg, self.addr_fact, self.btn_copy,
                      self.clinic, self.uch, self.doctor, self.phone, self.invalid,
                      self.weight]:
                w.setEnabled(False)

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
        if digits.startswith("8"):
            digits = "7" + digits[1:]
        if len(digits) >= 11:
            formatted = f"+7({digits[1:4]})-{digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
            self.phone.blockSignals(True)
            self.phone.setText(formatted)
            self.phone.blockSignals(False)

    def save(self):
        if not confirm(self, "Вы уверены, что хотите сохранить?"):
            return

        if not self.last.text().strip() or not self.first.text().strip():
            QMessageBox.warning(self, "Ошибка", "Заполните хотя бы фамилию и имя.")
            return

        weight_text = self.weight.text().strip().replace(",", ".")
        weight_val = None
        if weight_text:
            try:
                weight_val = float(weight_text)
            except ValueError:
                QMessageBox.warning(self, "Ошибка", "Вес должен быть числом, например 28.5")
                return

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()

        data = (
            self.last.text(), self.first.text(), self.middle.text(),
            self.birth.text(), self.mkb.currentText(),
            self.gender.currentText(),
            self.addr_reg.text(), self.addr_fact.text(),
            self.clinic.currentText(), self.uch.text(),
            self.doctor.text(), self.phone.text(),
            int(self.invalid.isChecked()), weight_val
        )

        if self.p:
            cur.execute("""
            UPDATE patients SET
            last=?,first=?,middle=?,birth=?,mkb=?,gender=?,
            address_reg=?,address_fact=?,clinic=?,uch=?,doctor=?,phone=?,invalid=?,weight=?
            WHERE id=?
            """, (*data, self.p[0]))
            self.saved_id = self.p[0]
        else:
            cur.execute("""
            INSERT INTO patients(
            last,first,middle,birth,mkb,gender,address_reg,address_fact,
            clinic,uch,doctor,phone,invalid,weight
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, data)
            self.saved_id = cur.lastrowid

        con.commit()
        con.close()
        self.accept()

    def cancel(self):
        if confirm(self, "Вы уверены, что хотите отменить?"):
            self.reject()

    def load_data(self):
        p = self.p
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
        self.blocks = []  # list of dicts: type, start, count, file, db_id, btn

        self.setWindowTitle("Анализы — просмотр" if readonly else "Анализы")
        self.resize(750, 550)

        l = QVBoxLayout()

        self.t = QTableWidget()
        self.t.setColumnCount(5)
        self.t.setHorizontalHeaderLabels(
            ["Анализ", "Показатель", "Результат", "Ед. изм.", "Файл"]
        )
        self.t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        l.addWidget(self.t)

        btn_row = QHBoxLayout()
        if not readonly:
            add_btn = QPushButton("+")
            add_btn.setFixedWidth(40)
            add_btn.clicked.connect(self.on_add)
            btn_row.addWidget(add_btn)

        btn_row.addStretch()

        if readonly:
            close_btn = QPushButton("Закрыть")
            close_btn.setProperty("role", "secondary")
            close_btn.clicked.connect(self.accept)
            btn_row.addWidget(close_btn)
        else:
            save_btn = QPushButton("Сохранить")
            cancel_btn = QPushButton("Отмена")
            cancel_btn.setProperty("role", "secondary")
            save_btn.clicked.connect(self.save)
            cancel_btn.clicked.connect(self.cancel)
            btn_row.addWidget(save_btn)
            btn_row.addWidget(cancel_btn)

        l.addLayout(btn_row)
        self.setLayout(l)

        self.load_existing()

    def load_existing(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT id, type, data, file FROM analyses WHERE pid=? ORDER BY id", (self.pid,))
        rows = cur.fetchall()
        con.close()
        for rid, atype, data_json, file_path in rows:
            data = json.loads(data_json) if data_json else {}
            if atype not in ANALYSIS_DEFS:
                continue
            self.add_block(atype, values=data, file_path=file_path, db_id=rid)

    def on_add(self):
        atype, ok = QInputDialog.getItem(
            self, "Добавить анализ", "Выберите тип анализа:",
            list(ANALYSIS_DEFS.keys()), 0, False
        )
        if ok and atype:
            self.add_block(atype)

    def add_block(self, atype, values=None, file_path=None, db_id=None):
        defs = ANALYSIS_DEFS[atype]
        start = self.t.rowCount()
        self.t.setRowCount(start + len(defs))

        for i, (label, unit) in enumerate(defs):
            row = start + i

            item0 = QTableWidgetItem(atype if i == 0 else "")
            item0.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.t.setItem(row, 0, item0)

            item1 = QTableWidgetItem(label)
            item1.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.t.setItem(row, 1, item1)

            val = values.get(label, "") if values else ""
            item2 = QTableWidgetItem(val)
            if self.readonly:
                item2.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.t.setItem(row, 2, item2)

            item3 = QTableWidgetItem(unit)
            item3.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.t.setItem(row, 3, item3)

        self.t.setSpan(start, 0, len(defs), 1)
        self.t.setSpan(start, 4, len(defs), 1)

        idx = len(self.blocks)
        btn = QPushButton()
        self.blocks.append({
            "type": atype, "start": start, "count": len(defs),
            "file": file_path, "db_id": db_id, "btn": btn
        })
        self.update_file_btn(idx)
        btn.clicked.connect(partial(self.handle_file, idx))
        self.t.setCellWidget(start, 4, btn)

    def update_file_btn(self, idx):
        block = self.blocks[idx]
        btn = block["btn"]
        if block["file"]:
            btn.setText(f"📎 {os.path.basename(block['file'])}")
        else:
            btn.setText("Нет файла" if self.readonly else "Добавить файл")
            if self.readonly:
                btn.setEnabled(False)

    def handle_file(self, idx):
        block = self.blocks[idx]

        if self.readonly:
            if block["file"] and os.path.exists(block["file"]):
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(block["file"])))
            else:
                QMessageBox.information(self, "Файл", "Файл не найден.")
            return

        if block["file"]:
            box = QMessageBox(self)
            box.setWindowTitle("Файл анализа")
            box.setText("Файл уже прикреплён. Что сделать?")
            open_btn = box.addButton("Открыть", QMessageBox.ButtonRole.AcceptRole)
            replace_btn = box.addButton("Заменить", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked == open_btn:
                if os.path.exists(block["file"]):
                    QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(block["file"])))
                else:
                    QMessageBox.warning(self, "Файл", "Файл не найден на диске.")
                return
            elif clicked != replace_btn:
                return

        path, _ = QFileDialog.getOpenFileName(self, "Выберите файл анализа")
        if not path:
            return

        os.makedirs(FILES_DIR, exist_ok=True)
        basename = os.path.basename(path)
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        dest_name = f"pid{self.pid}_{block['type']}_{stamp}_{basename}"
        dest_name = re.sub(r"[^\w\.\-А-Яа-яЁё]", "_", dest_name)
        dest_path = os.path.join(FILES_DIR, dest_name)
        shutil.copy(path, dest_path)

        block["file"] = dest_path
        self.update_file_btn(idx)

    def save(self):
        if not confirm(self, "Вы уверены, что хотите сохранить?"):
            return

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()

        for block in self.blocks:
            data = {}
            for i in range(block["count"]):
                row = block["start"] + i
                label = self.t.item(row, 1).text()
                value = self.t.item(row, 2).text()
                data[label] = value
            data_json = json.dumps(data, ensure_ascii=False)

            if block["db_id"]:
                cur.execute(
                    "UPDATE analyses SET data=?, file=? WHERE id=?",
                    (data_json, block["file"], block["db_id"])
                )
            else:
                cur.execute(
                    "INSERT INTO analyses(pid, type, data, file, created_at) VALUES(?,?,?,?,?)",
                    (self.pid, block["type"], data_json, block["file"],
                     datetime.now().strftime("%d.%m.%Y %H:%M"))
                )

        con.commit()
        con.close()
        self.accept()

    def cancel(self):
        if confirm(self, "Вы уверены, что хотите отменить?"):
            self.reject()


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
        cfg = self.current_drug_cfg()
        stage = self.stage_box.currentText()
        vial_mg = cfg["vial_mg"]
        weight = self.weight_kg

        total_mg = None
        dose_label = "-"
        interval_weeks = None

        if not weight:
            self.res_dose.setText("Доза: невозможно рассчитать без веса пациента")
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
            "date": self.date_edit.text(),
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
        self.resize(450, 320)

        self.layout_main = QVBoxLayout()
        self.info_box = QVBoxLayout()
        self.layout_main.addLayout(self.info_box)

        btn_row = QHBoxLayout()
        self.passport_btn = QPushButton("Паспортная часть")
        self.analyses_btn = QPushButton("Анализы")
        self.therapy_btn = QPushButton("Препараты")
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
        win.exec()

    def open_therapy(self):
        readonly = self.mode == "view"
        win = TherapyWindow(self.pid, readonly=readonly)
        win.exec()


# =========================
# MAIN
# =========================
class App(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("МедРегистр")
        self.resize(980, 560)

        l = QVBoxLayout()
        l.setContentsMargins(16, 16, 16, 16)
        l.setSpacing(10)

        title = QLabel("МедРегистр — детская гастроэнтерология")
        title.setProperty("role", "title")
        l.addWidget(title)

        top_row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Поиск по ФИО или диагнозу (МКБ-10)...")
        self.search_box.textChanged.connect(self.apply_filter)
        top_row.addWidget(self.search_box)

        reports_btn = QPushButton("📊 Отчёты")
        reports_btn.setProperty("role", "accent")
        reports_btn.setFixedWidth(120)
        reports_btn.clicked.connect(self.open_reports)
        top_row.addWidget(reports_btn)
        l.addLayout(top_row)

        self.t = QTableWidget()
        self.t.setColumnCount(4)
        self.t.setHorizontalHeaderLabels(["ФИО", "Дата рождения", "МКБ-10", "Пол"])
        self.t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.t.setAlternatingRowColors(True)
        self.t.doubleClicked.connect(self.view)
        l.addWidget(self.t)

        self.status_label = QLabel("")
        self.status_label.setProperty("role", "muted")
        l.addWidget(self.status_label)

        h = QHBoxLayout()
        add = QPushButton("Добавить")
        edit = QPushButton("Редактировать")
        edit.setProperty("role", "secondary")
        view = QPushButton("Просмотреть")
        view.setProperty("role", "secondary")

        add.clicked.connect(self.add)
        edit.clicked.connect(self.edit)
        view.clicked.connect(self.view)

        h.addWidget(add)
        h.addWidget(edit)
        h.addWidget(view)
        l.addLayout(h)

        self.setLayout(l)
        self.data = []      # все пациенты
        self.filtered = []  # то, что реально отображается в таблице сейчас
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
        win = ReportsWindow()
        win.exec()


# =========================
# RUN
# =========================
if __name__ == "__main__":
    init_db()
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE_SHEET)
    w = App()
    w.show()
    sys.exit(app.exec())
