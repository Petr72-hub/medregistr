import sys, os, shutil, sqlite3, math, uuid
from datetime import datetime, date
from PyQt6.QtWidgets import *
from PyQt6.QtCore import Qt, QDate

DB_NAME = "children.db"
FILES_DIR = "files"
if not os.path.exists(FILES_DIR): os.makedirs(FILES_DIR)

MKB_CODES = ["K29 Гастрит", "K21 ГЭРБ", "K52 Гастроэнтерит", "K25 Язва желудка", "K26 Язва 12ПК", "K58 СРК", "E11 Диабет", "B15 Гепатит A"]
CLINICS = ["Поликлиника №1", "Поликлиника №2", "Поликлиника №5"]

ANALYSIS_PARAMS = {
    "ОАК": [("Гемоглобин", "г/л"), ("Гематокрит", "%"), ("Эритроциты", "*10^12"), ("Цветовой показатель", ""), ("Ретикулоциты", "%"), 
            ("Тромбоциты", "*10^9"), ("СОЭ", "мм/ч"), ("Лейкоциты", "*10^9"), ("Палочкоядерные гранулоциты", "%"), 
            ("Сегментоядерные гранулоциты", "%"), ("Эозинофилы", "%"), ("Базофилы", "%"), ("Лимфоциты", "%"), ("Моноциты", "%")],
    "ОАМ": [("Цвет", ""), ("Прозрачность", ""), ("Плотность", ""), ("Реакция pH", ""), ("Запах", ""), 
            ("Белок", "г/л"), ("Глюкоза", "ммоль/л"), ("Кетоновые тела", ""), ("Билирубин", ""), ("Гемоглобин", ""), 
            ("Лейкоциты", "в П/З"), ("Эритроциты", "в П/З"), ("Эпителий", ""), ("Цилиндры", ""), ("Соли", ""), 
            ("Бактерии и нитраты", ""), ("Грибок", "")],
    "Биохимия крови": [("Сахар крови", "ммоль/л"), ("Креатинин", "ммоль/л"), ("Мочевина", "ммоль/л"), ("Билирубин общий", "ммоль/л"), 
                       ("АлАТ", "МЕ/л"), ("АсАт", "МЕ/л"), ("Общий белок", "г/л"), ("Амилаза", "МЕ/л"), 
                       ("Холестерин", "ммоль/л"), ("Щелочная фосфатаза", "МЕ/л"), ("Кальций", "ммоль/л"), ("Калий", "ммоль/л"), 
                       ("Фосфор", "ммоль/л"), ("Натрий", "ммоль/л"), ("Хлор", "ммоль/л"), ("КФК", "МЕ/л"), ("ГГТ", "ед.")]
}

def init_db():
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    
    # Patients & Analyses
    cur.execute("CREATE TABLE IF NOT EXISTS patients (id INTEGER PRIMARY KEY)")
    cols = ['last TEXT', 'first TEXT', 'middle TEXT', 'birth TEXT', 'mkb TEXT', 
            'gender TEXT DEFAULT ""', 'address_reg TEXT', 'address_fact TEXT', 
            'clinic TEXT', 'district TEXT', 'pediatrician TEXT', 'phone TEXT', 'is_disabled INTEGER DEFAULT 0']
    for col in cols:
        name = col.split()[0]
        if not cur.execute("SELECT name FROM pragma_table_info('patients') WHERE name=?", (name,)).fetchone():
            cur.execute(f"ALTER TABLE patients ADD COLUMN {col}")
    cur.execute("CREATE TABLE IF NOT EXISTS analysis_headers (id TEXT PRIMARY KEY, pid INTEGER, type_name TEXT, file_path TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS analysis_details (id INTEGER PRIMARY KEY, header_id TEXT, param_name TEXT, result TEXT, unit TEXT)")

    # Drugs & Inventory & Therapies
    cur.execute("CREATE TABLE IF NOT EXISTS drugs (id INTEGER PRIMARY KEY, name TEXT, strength INTEGER, form TEXT, pack_size INTEGER)")
    cur.execute("CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY, drug_id INTEGER UNIQUE, packs INTEGER)")
    cur.execute("CREATE TABLE IF NOT EXISTS therapies (id INTEGER PRIMARY KEY, pid INTEGER, drug_id INTEGER, daily_dose INTEGER, doses_per_day INTEGER, duration_days INTEGER, start_date TEXT)")

    # Seed inventory if empty
    if not cur.execute("SELECT COUNT(*) FROM inventory").fetchone()[0]:
        seeds = [
            ("Петриссимин", 250, "Таблетки", 10, 10), ("Петриссимин", 250, "Таблетки", 20, 40), ("Петриссимин", 250, "Таблетки", 40, 5),
            ("Петриссимин", 250, "Капсулы", 10, 10), ("Петриссимин", 250, "Капсулы", 20, 40), ("Петриссимин", 250, "Капсулы", 40, 5),
            ("Петриссимин форте", 500, "Капсулы", 5, 10), ("Петриссимин форте", 500, "Капсулы", 30, 15),
            ("Питрисун", 50, "Таблетки", 10, 50), ("Питрисун", 50, "Таблетки", 20, 3)
        ]
        for name, strg, form, sz, packs in seeds:
            cur.execute("INSERT INTO drugs(name, strength, form, pack_size) VALUES(?,?,?,?)", (name, strg, form, sz))
            did = cur.lastrowid
            cur.execute("INSERT INTO inventory(drug_id, packs) VALUES(?,?)", (did, packs))
            
    con.commit()
    con.close()

def format_phone(text):
    digits = ''.join(filter(str.isdigit, text))
    if digits.startswith('8'): digits = '7' + digits[1:]
    if digits.startswith('7'): digits = digits[1:]
    d = digits[:10]
    res = "+7"
    if len(d) >= 3: res += f"({d[:3]})"
    elif len(d) > 0: res += f"({d}"
    if len(d) >= 6: res += f"-{d[3:6]}"
    elif len(d) > 3: res += f"-{d[3:]}"
    if len(d) >= 8: res += f"-{d[6:8]}"
    elif len(d) > 6: res += f"-{d[6:]}"
    if len(d) >= 10: res += f"-{d[8:]}"
    elif len(d) > 8: res += f"-{d[8:]}"
    return res

class SmartCombo(QComboBox):
    def __init__(self, data, parent=None):
        super().__init__(parent)
        self.data = data
        self.setEditable(True)
        self.addItems(data)
        self.setPlaceholderText("Выберите...")
        self.lineEdit().textEdited.connect(self.filter)
    def filter(self, text):
        self.blockSignals(True)
        self.clear()
        res = [x for x in self.data if text.lower() in x.lower()][:10]
        self.addItems(res)
        self.setEditText(text)
        self.blockSignals(False)
        self.lineEdit().setCursorPosition(len(text))

class TherapyDialog(QDialog):
    def __init__(self, pid, parent=None):
        super().__init__(parent)
        self.pid = pid
        self.setWindowTitle(f"Терапия пациента (ID: {pid})")
        self.resize(800, 500)
        layout = QVBoxLayout()
        form = QFormLayout()
        self.med_combo = QComboBox()
        self.load_drugs()
        form.addRow("Препарат:", self.med_combo)
        self.scheme_combo = QComboBox()
        self.scheme_combo.addItems(["250 мг (1 прием/день)", "500 мг (2 приема/день)", "750 мг (3 приема/день)"])
        form.addRow("Схема:", self.scheme_combo)
        self.dur_combo = QComboBox()
        self.dur_combo.addItems(["5 дней", "10 дней", "14 дней"])
        form.addRow("Длительность:", self.dur_combo)
        self.start_date = QDateEdit()
        self.start_date.setCalendarPopup(True)
        self.start_date.setDate(QDate.currentDate())
        form.addRow("Дата начала:", self.start_date)
        layout.addLayout(form)
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["Препарат", "Схема", "Дозы/день", "Дней", "Начало", "Удалить"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        btn_layout = QHBoxLayout()
        add = QPushButton("Добавить"); add.clicked.connect(self.add_therapy); btn_layout.addWidget(add)
        close = QPushButton("Закрыть"); close.clicked.connect(self.reject); btn_layout.addWidget(close)
        layout.addLayout(btn_layout)
        self.setLayout(layout)
        self.load_therapies()

    def load_drugs(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT id, name, strength, form FROM drugs")
        for row in cur.fetchall():
            self.med_combo.addItem(f"{row[1]} {row[2]}мг ({row[3]})", row[0])
        con.close()

    def load_therapies(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT t.id, d.name, d.strength, t.doses_per_day, t.duration_days, t.start_date FROM therapies t JOIN drugs d ON t.drug_id = d.id WHERE t.pid=?", (self.pid,))
        data = cur.fetchall()
        con.close()
        self.table.setRowCount(len(data))
        for r, row in enumerate(data):
            self.table.setItem(r, 0, QTableWidgetItem(f"{row[1]} {row[2]}мг"))
            self.table.setItem(r, 1, QTableWidgetItem(f"{row[2]*row[3]} мг"))
            self.table.setItem(r, 2, QTableWidgetItem(str(row[3])))
            self.table.setItem(r, 3, QTableWidgetItem(f"{row[4]} дн."))
            self.table.setItem(r, 4, QTableWidgetItem(row[5]))
            del_btn = QPushButton("❌")
            del_btn.clicked.connect(lambda checked, tid=row[0]: self.delete_therapy(tid))
            self.table.setCellWidget(r, 5, del_btn)

    def add_therapy(self):
        if self.med_combo.currentIndex() == -1:
            QMessageBox.warning(self, "Ошибка", "Выберите препарат!")
            return
        drug_id = self.med_combo.currentData()
        scheme = self.scheme_combo.currentIndex()
        doses = scheme + 1
        daily_dose = 250 * doses
        duration = [5, 10, 14][self.dur_combo.currentIndex()]
        start = self.start_date.date().toString("yyyy-MM-dd")
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("INSERT INTO therapies(pid, drug_id, daily_dose, doses_per_day, duration_days, start_date) VALUES(?,?,?,?,?,?)",
                    (self.pid, drug_id, daily_dose, doses, duration, start))
        con.commit(); con.close()
        self.load_therapies()

    def delete_therapy(self, tid):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("DELETE FROM therapies WHERE id=?", (tid,))
        con.commit(); con.close()
        self.load_therapies()

class ForecastDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Прогноз остатков (Учет упаковок)")
        self.resize(650, 550)
        layout = QVBoxLayout()
        
        form = QFormLayout()
        self.target_date = QDateEdit()
        self.target_date.setCalendarPopup(True)
        self.target_date.setDate(QDate.currentDate().addDays(30))
        form.addRow("Дата прогноза:", self.target_date)
        
        self.med_filter = QComboBox()
        self.med_filter.addItem("Все препараты")
        self.load_drugs()
        form.addRow("Препарат:", self.med_filter)
        layout.addLayout(form)
        
        self.result_label = QLabel("Выберите дату и нажмите рассчитать")
        self.result_label.setWordWrap(True)
        self.result_label.setStyleSheet("""
            QLabel {
                background-color: #ffffff;
                color: #000000;
                border: 1px solid #cccccc;
                border-radius: 6px;
                padding: 12px;
                font-size: 14px;
                line-height: 1.4;
            }
        """)
        layout.addWidget(self.result_label)
        
        btn = QPushButton("Рассчитать"); btn.clicked.connect(self.calculate); layout.addWidget(btn)
        close = QPushButton("Закрыть"); close.clicked.connect(self.reject); layout.addWidget(close)
        self.setLayout(layout)

    def load_drugs(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT DISTINCT name FROM drugs")
        for r in cur.fetchall(): self.med_filter.addItem(r[0])
        con.close()

    def calculate(self):
        target = self.target_date.date().toPyDate()
        selected_name = self.med_filter.currentText()
        
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT d.id, d.name, d.form, d.pack_size, i.packs FROM drugs d JOIN inventory i ON d.id = i.drug_id")
        inventory = cur.fetchall()
        cur.execute("SELECT t.drug_id, t.daily_dose, t.duration_days, t.start_date FROM therapies t")
        therapies = cur.fetchall()
        con.close()

        # 1. Считаем общее количество таблеток, необходимое каждому drug_id
        pills_needed_per_drug = {}
        for did, daily_dose, duration, start_str in therapies:
            try:
                start = datetime.strptime(start_str, "%Y-%m-%d").date()
            except ValueError:
                continue

            days_active = (target - start).days + 1
            if days_active <= 0: continue

            days_taken = min(duration, max(0, days_active))
            if days_taken <= 0: continue

            con2 = sqlite3.connect(DB_NAME)
            res = con2.execute("SELECT strength FROM drugs WHERE id=?", (did,)).fetchone()
            con2.close()
            if not res: continue
            
            pills_per_day = math.ceil(daily_dose / res[0])
            total_needed = pills_per_day * days_taken
            pills_needed_per_drug[did] = pills_needed_per_drug.get(did, 0) + total_needed

        # 2. Группируем склад по названию препарата
        inventory_by_name = {}
        for did, name, form, psize, packs in inventory:
            if selected_name != "Все препараты" and name != selected_name:
                continue
            if name not in inventory_by_name:
                inventory_by_name[name] = []
            inventory_by_name[name].append({'did': did, 'form': form, 'psize': psize, 'packs': packs})

        remaining_msg = []

        for name, items in inventory_by_name.items():
            # Суммируем потребность по всем формам этого препарата
            total_need_for_name = sum(pills_needed_per_drug.get(it['did'], 0) for it in items)

            # Если потребность нулевая, просто выводим текущие остатки
            if total_need_for_name == 0:
                lines = [f"• {name} ({it['form']}, {it['psize']}шт/уп): {it['packs']} уп." for it in items]
                remaining_msg.append("\n".join(lines))
                continue

            # Распределяем потребность по упаковкам (от мелких к крупным)
            sorted_items = sorted(items, key=lambda x: x['psize'])
            temp_need = total_need_for_name
            lines = []

            for item in sorted_items:
                packs_avail = item['packs']
                psize = item['psize']
                
                if temp_need <= 0:
                    lines.append(f"• {name} ({item['form']}, {psize}шт/уп): {packs_avail} уп.")
                    continue

                # Сколько таблеток в этом типе упаковки доступно
                pills_avail = packs_avail * psize
                
                if pills_avail <= temp_need:
                    temp_need -= pills_avail
                    lines.append(f"• {name} ({item['form']}, {psize}шт/уп): 0 уп. (разобрано)")
                else:
                    # Берем целое количество пачек, чтобы покрыть остаток потребности
                    packs_to_take = math.ceil(temp_need / psize)
                    temp_need = 0
                    packs_left = packs_avail - packs_to_take
                    lines.append(f"• {name} ({item['form']}, {psize}шт/уп): {packs_left} уп.")

            if not lines:
                lines.append(f"• {name}: 0 уп. (закончилось)")
            remaining_msg.append("\n".join(lines))

        self.result_label.setText("\n\n".join(remaining_msg) if remaining_msg else "Нет данных для расчёта.")

class InventoryDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Учет препаратов")
        self.resize(900, 500)
        layout = QVBoxLayout()
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["Название", "Сила (мг)", "Форма", "В уп. (шт)", "Упаковок", "Действие"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        h = QHBoxLayout()
        add = QPushButton("➕ Новая поставка"); add.clicked.connect(self.add_stock)
        forecast = QPushButton("📊 Прогноз остатков"); forecast.clicked.connect(lambda: ForecastDialog(self).exec())
        close = QPushButton("Закрыть"); close.clicked.connect(self.reject)
        h.addWidget(add); h.addWidget(forecast); h.addStretch(); h.addWidget(close)
        layout.addLayout(h)
        self.setLayout(layout)
        self.load_stock()

    def load_stock(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT d.name, d.strength, d.form, d.pack_size, i.packs, d.id FROM drugs d JOIN inventory i ON d.id = i.drug_id")
        data = cur.fetchall(); con.close()
        self.table.setRowCount(len(data))
        for r, row in enumerate(data):
            for c, val in enumerate(row[:5]):
                self.table.setItem(r, c, QTableWidgetItem(str(val)))
            btn = QPushButton("Добавить"); btn.clicked.connect(lambda checked, did=row[5]: self.quick_add(did))
            self.table.setCellWidget(r, 5, btn)

    def add_stock(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT id, name, strength, form, pack_size FROM drugs")
        drugs = cur.fetchall(); con.close()
        if not drugs: return
        dlg = QDialog(self)
        dlg.setWindowTitle("Добавить поставку")
        l = QFormLayout()
        combo = QComboBox()
        for d in drugs: combo.addItem(f"{d[1]} {d[2]}мг {d[3]} ({d[4]}шт)", d[0])
        l.addRow("Препарат:", combo)
        packs_in = QSpinBox(); packs_in.setMinimum(1); packs_in.setMaximum(9999); packs_in.setValue(1)
        l.addRow("Количество упаковок:", packs_in)
        btn = QPushButton("Сохранить")
        l.addRow(btn)
        dlg.setLayout(l)
        btn.clicked.connect(lambda: self.save_stock(combo.currentData(), packs_in.value(), dlg))
        dlg.exec()

    def quick_add(self, did):
        n, ok = QInputDialog.getInt(self, "Добавить", "Сколько упаковок добавить?", 1, 1)
        if ok: self.save_stock(did, n)

    def save_stock(self, did, packs, dlg=None):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT packs FROM inventory WHERE drug_id=?", (did,))
        res = cur.fetchone()
        if res:
            cur.execute("UPDATE inventory SET packs=? WHERE drug_id=?", (res[0]+packs, did))
        else:
            cur.execute("INSERT INTO inventory(drug_id, packs) VALUES(?,?)", (did, packs))
        con.commit(); con.close()
        self.load_stock()
        if dlg: dlg.accept()

class PatientEditDialog(QDialog):
    def __init__(self, patient=None, parent=None):
        super().__init__(parent)
        self.patient = patient
        self.setWindowTitle("Редактирование паспортной части")
        self.resize(600, 600)
        main_layout = QVBoxLayout()
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll_widget = QWidget(); layout = QVBoxLayout(scroll_widget)
        
        self.last = QLineEdit(); self.last.setPlaceholderText("Фамилия")
        self.first = QLineEdit(); self.first.setPlaceholderText("Имя")
        self.middle = QLineEdit(); self.middle.setPlaceholderText("Отчество")
        h_fio = QHBoxLayout(); h_fio.addWidget(self.last); h_fio.addWidget(self.first); h_fio.addWidget(self.middle)
        layout.addLayout(h_fio)
        
        self.birth = QLineEdit(); self.birth.setPlaceholderText("ДД.ММ.ГГГГ"); self.birth.textChanged.connect(self.format_date)
        layout.addWidget(self.birth)
        self.mkb = SmartCombo(MKB_CODES); layout.addWidget(self.mkb)
        self.gender = QComboBox(); self.gender.addItems(["", "Мальчик", "Девочка"]); layout.addWidget(self.gender)
        self.addr_reg = QLineEdit(); self.addr_reg.setPlaceholderText("Адрес регистрации"); layout.addWidget(self.addr_reg)
        self.addr_fact = QLineEdit(); self.addr_fact.setPlaceholderText("Адрес фактический"); layout.addWidget(self.addr_fact)
        layout.addWidget(QPushButton("АР совпадает АФ").clicked.connect(lambda: self.addr_fact.setText(self.addr_reg.text())))
        self.clinic = QComboBox(); self.clinic.addItems([""] + CLINICS); layout.addWidget(self.clinic)
        self.district = QLineEdit(); self.district.setPlaceholderText("Участок"); layout.addWidget(self.district)
        self.pediatrician = QLineEdit(); self.pediatrician.setPlaceholderText("ФИО педиатра"); layout.addWidget(self.pediatrician)
        self.phone = QLineEdit(); self.phone.setPlaceholderText("+7(XXX)-XXX-XX-XX"); self.phone.textEdited.connect(self.update_phone); layout.addWidget(self.phone)
        self.is_disabled = QCheckBox("Ребёнок-инвалид"); layout.addWidget(self.is_disabled)
        
        scroll.setWidget(scroll_widget); main_layout.addWidget(scroll)
        self.btn_box = QHBoxLayout()
        self.save_btn = QPushButton("Сохранить"); self.save_btn.clicked.connect(self.confirm_save)
        self.cancel_btn = QPushButton("Отмена"); self.cancel_btn.clicked.connect(self.reject)
        self.btn_box.addWidget(self.save_btn); self.btn_box.addWidget(self.cancel_btn)
        main_layout.addLayout(self.btn_box)
        self.setLayout(main_layout)
        if patient: self.load_data(patient)

    def format_date(self):
        t = self.birth.text().replace('.', '').replace('-', '')
        if len(t) > 8: t = t[:8]
        if len(t) >= 4 and t.isdigit():
            self.birth.blockSignals(True)
            self.birth.setText(f"{t[:2]}.{t[2:4]}.{t[4:]}")
            self.birth.blockSignals(False)
    def update_phone(self):
        formatted = format_phone(self.phone.text())
        self.phone.blockSignals(True); self.phone.setText(formatted); self.phone.blockSignals(False)
        self.phone.setCursorPosition(len(formatted))
    def load_data(self, p):
        self.last.setText(str(p[1] or "")); self.first.setText(str(p[2] or "")); self.middle.setText(str(p[3] or ""))
        self.birth.setText(str(p[4] or "")); self.mkb.setCurrentText(str(p[5] or "")); self.gender.setCurrentText(str(p[6] or ""))
        self.addr_reg.setText(str(p[7] or "")); self.addr_fact.setText(str(p[8] or "")); self.clinic.setCurrentText(str(p[9] or ""))
        self.district.setText(str(p[10] or "")); self.pediatrician.setText(str(p[11] or "")); self.phone.setText(str(p[12] or ""))
        self.is_disabled.setChecked(bool(p[13]))
    def confirm_save(self):
        if QMessageBox.question(self, "Подтверждение", "Вы уверены, что хотите сохранить?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.save()
    def save(self):
        if not self.last.text().strip() or not self.first.text().strip():
            QMessageBox.warning(self, "Ошибка", "Заполните Фамилию и Имя!"); return
        con = sqlite3.connect(DB_NAME); cur = con.cursor()
        try:
            data = (self.last.text(), self.first.text(), self.middle.text(), self.birth.text(), 
                    self.mkb.currentText(), self.gender.currentText(), self.addr_reg.text(), 
                    self.addr_fact.text(), self.clinic.currentText(), self.district.text(), 
                    self.pediatrician.text(), self.phone.text(), int(self.is_disabled.isChecked()))
            if self.patient:
                cur.execute("UPDATE patients SET last=?,first=?,middle=?,birth=?,mkb=?,gender=?,address_reg=?,address_fact=?,clinic=?,district=?,pediatrician=?,phone=?,is_disabled=? WHERE id=?", (*data, self.patient[0]))
            else:
                cur.execute("INSERT INTO patients(last,first,middle,birth,mkb,gender,address_reg,address_fact,clinic,district,pediatrician,phone,is_disabled) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", data)
            con.commit(); QMessageBox.information(self, "Успех", "Данные сохранены!"); self.accept()
        except Exception as e:
            con.rollback(); QMessageBox.critical(self, "Ошибка БД", str(e))
        finally: con.close()

class AnalysesDialog(QDialog):
    def __init__(self, pid, parent=None):
        super().__init__(parent)
        self.pid = pid
        self.setWindowTitle(f"Анализы пациента (ID: {pid})")
        self.resize(1000, 700)
        layout = QVBoxLayout()
        self.table = QTableWidget()
        self.table.setColumnCount(7) 
        self.table.setHorizontalHeaderLabels(["Тип анализа", "Параметр", "Результат", "Ед. изм.", "Файл", "ID блока", "Путь"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.hideColumn(5)
        self.table.hideColumn(6)
        layout.addWidget(self.table)
        h = QHBoxLayout()
        add_btn = QPushButton("+"); add_btn.setFixedWidth(40); add_btn.clicked.connect(self.add_analysis); h.addWidget(add_btn)
        h.addStretch()
        save_btn = QPushButton("Сохранить"); save_btn.clicked.connect(self.confirm_save); h.addWidget(save_btn)
        cancel_btn = QPushButton("Отмена"); cancel_btn.clicked.connect(self.confirm_cancel); h.addWidget(cancel_btn)
        layout.addLayout(h)
        self.setLayout(layout)
        self.load_analyses()

    def load_analyses(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT id, type_name, file_path FROM analysis_headers WHERE pid=?", (self.pid,))
        headers = cur.fetchall()
        self.table.setRowCount(0)
        for h_id, type_name, file_path in headers:
            cur.execute("SELECT param_name, result, unit FROM analysis_details WHERE header_id=?", (h_id,))
            params = cur.fetchall()
            self.add_block_to_table(type_name, h_id, file_path, params)
        con.close()

    def add_block_to_table(self, type_name, h_id, file_path, params):
        start_row = self.table.rowCount()
        params_list = ANALYSIS_PARAMS.get(type_name, [])
        for i, (p_name, p_unit) in enumerate(params_list):
            r = start_row + i
            self.table.insertRow(r)
            saved_res = next((res for pn, res, u in params if pn == p_name), "")
            if i == 0:
                self.table.setItem(r, 0, QTableWidgetItem(type_name))
                btn = QPushButton("📎" if not file_path else "📄"); btn.setFixedWidth(40)
                btn.clicked.connect(lambda checked, row=start_row: self.attach_file(row))
                self.table.setCellWidget(r, 4, btn)
                self.table.setItem(r, 6, QTableWidgetItem(file_path if file_path else ""))
            self.table.setItem(r, 1, QTableWidgetItem(p_name))
            self.table.setItem(r, 2, QTableWidgetItem(saved_res))
            self.table.setItem(r, 3, QTableWidgetItem(p_unit))
            self.table.setItem(r, 5, QTableWidgetItem(h_id))
            self.table.setRowHeight(r, 35)

    def add_analysis(self):
        type_name, ok = QInputDialog.getItem(self, "Добавить анализ", "Выберите тип:", list(ANALYSIS_PARAMS.keys()), 0, False)
        if not ok or not type_name: return
        h_id = str(uuid.uuid4())
        self.add_block_to_table(type_name, h_id, "", [])
        self.table.scrollToBottom()

    def attach_file(self, row):
        path = QFileDialog.getOpenFileName(self, "Выберите файл")[0]
        if path:
            name = os.path.basename(path)
            dest = os.path.join(FILES_DIR, f"pid{self.pid}_{uuid.uuid4().hex}_{name}")
            try: shutil.copy(path, dest)
            except: dest = path
            self.table.cellWidget(row, 4).setText("📄")
            self.table.item(row, 6).setText(dest)
            QMessageBox.information(self, "Файл", f"Файл успешно сохранён:\n{name}")

    def confirm_save(self):
        if QMessageBox.question(self, "Подтверждение", "Сохранить анализы?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.save()

    def confirm_cancel(self):
        if QMessageBox.question(self, "Подтверждение", "Отменить изменения?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.reject()

    def save(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        try:
            cur.execute("DELETE FROM analysis_details WHERE header_id IN (SELECT id FROM analysis_headers WHERE pid=?)", (self.pid,))
            cur.execute("DELETE FROM analysis_headers WHERE pid=?", (self.pid,))
            current_h_id = None
            current_type_name = None
            for r in range(self.table.rowCount()):
                hid_cell = self.table.item(r, 5)
                if not hid_cell: continue
                h_id = hid_cell.text()
                type_cell = self.table.item(r, 0)
                if type_cell: current_type_name = type_cell.text()
                param = self.table.item(r, 1).text()
                res_item = self.table.item(r, 2)
                res = res_item.text() if res_item else ""
                unit = self.table.item(r, 3).text()
                if h_id != current_h_id:
                    current_h_id = h_id
                    file_cell = self.table.item(r, 6)
                    f_path = file_cell.text() if file_cell else ""
                    cur.execute("INSERT INTO analysis_headers VALUES(?,?,?,?)", (h_id, self.pid, current_type_name, f_path))
                cur.execute("INSERT INTO analysis_details(header_id, param_name, result, unit) VALUES(?,?,?,?)", (h_id, param, res, unit))
            con.commit()
            QMessageBox.information(self, "Успех", "Анализы сохранены!"); self.accept()
        except Exception as e:
            con.rollback(); QMessageBox.critical(self, "Ошибка", str(e))
        finally: con.close()

class PatientHubDialog(QDialog):
    def __init__(self, patient, mode="edit", parent=None):
        super().__init__(parent)
        self.patient = patient
        self.mode = mode
        title = "Редактирование" if mode == "edit" else "Просмотр"
        self.setWindowTitle(f"{title}: {patient[1]} {patient[2]} {patient[3]}")
        self.resize(500, 350)
        layout = QVBoxLayout()
        info = QGroupBox("Основная информация")
        v = QVBoxLayout()
        v.addWidget(QLabel(f"ФИО: {patient[1]} {patient[2]} {patient[3]}"))
        v.addWidget(QLabel(f"Дата рождения: {patient[4]}"))
        v.addWidget(QLabel(f"Диагноз: {patient[5]}"))
        v.addWidget(QLabel(f"Пол: {patient[6]}"))
        info.setLayout(v); layout.addWidget(info)
        
        btn_layout = QHBoxLayout()
        self.pass_btn = QPushButton("Паспортная часть"); self.pass_btn.clicked.connect(self.open_passport); btn_layout.addWidget(self.pass_btn)
        self.ther_btn = QPushButton("Терапия"); self.ther_btn.clicked.connect(self.open_therapy); btn_layout.addWidget(self.ther_btn)
        self.anal_btn = QPushButton("Анализы"); self.anal_btn.clicked.connect(self.open_analyses); btn_layout.addWidget(self.anal_btn)
        self.fed_btn = QPushButton("Фед. центр"); self.fed_btn.clicked.connect(lambda: QMessageBox.information(self, "Инфо", "В разработке")); btn_layout.addWidget(self.fed_btn)
        layout.addLayout(btn_layout)
        if self.mode == "edit":
            close = QPushButton("Закрыть"); close.clicked.connect(self.reject); layout.addWidget(close)
        self.setLayout(layout)

    def open_passport(self):
        dlg = PatientEditDialog(self.patient, self)
        if self.mode == "view":
            for w in dlg.findChildren(QLineEdit): w.setReadOnly(True)
            for w in dlg.findChildren(QComboBox): w.setEnabled(False)
            for w in dlg.findChildren(QCheckBox): w.setEnabled(False)
            dlg.save_btn.hide(); dlg.cancel_btn.hide()
            close_btn = QPushButton("Закрыть"); close_btn.clicked.connect(dlg.reject); dlg.btn_box.addWidget(close_btn)
        dlg.exec()
        if dlg.result(): self.update_patient()

    def open_therapy(self):
        TherapyDialog(self.patient[0], self).exec()
    def open_analyses(self):
        AnalysesDialog(self.patient[0], self).exec()

    def update_patient(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT id, last, first, middle, birth, mkb, gender, address_reg, address_fact, clinic, district, pediatrician, phone, is_disabled FROM patients WHERE id=?", (self.patient[0],))
        self.patient = cur.fetchone(); con.close()

class App(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("МедРегистр Гастрология")
        self.resize(1100, 650)
        layout = QVBoxLayout()
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Поиск по ФИО или МКБ...")
        self.search_input.textChanged.connect(self.load)
        layout.addWidget(self.search_input)
        
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["ID", "ФИО", "Дата рождения", "МКБ-10", "Пол"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.hideColumn(0)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)
        
        h = QHBoxLayout()
        add = QPushButton("Добавить"); add.clicked.connect(self.add); h.addWidget(add)
        edit = QPushButton("Редактировать"); edit.clicked.connect(self.edit); h.addWidget(edit)
        view = QPushButton("Просмотреть"); view.clicked.connect(self.view); h.addWidget(view)
        h.addStretch()
        stock = QPushButton("💊 Учет препаратов"); stock.clicked.connect(lambda: InventoryDialog(self).exec()); h.addWidget(stock)
        layout.addLayout(h)
        self.setLayout(layout)
        self.load()

    def load(self, filter_text=""):
        con = sqlite3.connect(DB_NAME); cur = con.cursor()
        if filter_text.strip():
            cur.execute("SELECT id, last, first, middle, birth, mkb, gender FROM patients WHERE (last || ' ' || first || ' ' || middle) LIKE ? OR mkb LIKE ?", (f"%{filter_text}%", f"%{filter_text}%"))
        else:
            cur.execute("SELECT id, last, first, middle, birth, mkb, gender FROM patients")
        data = cur.fetchall(); con.close()
        self.table.setRowCount(len(data))
        for r, row in enumerate(data):
            fio = f"{row[1]} {row[2]} {row[3]}".strip()
            self.table.setItem(r, 0, QTableWidgetItem(str(row[0])))
            self.table.setItem(r, 1, QTableWidgetItem(fio))
            self.table.setItem(r, 2, QTableWidgetItem(row[4]))
            self.table.setItem(r, 3, QTableWidgetItem(row[5]))
            self.table.setItem(r, 4, QTableWidgetItem(row[6]))

    def get_patient(self):
        r = self.table.currentRow()
        if r == -1: return None
        pid = int(self.table.item(r, 0).text())
        con = sqlite3.connect(DB_NAME); cur = con.cursor()
        cur.execute("SELECT id, last, first, middle, birth, mkb, gender, address_reg, address_fact, clinic, district, pediatrician, phone, is_disabled FROM patients WHERE id=?", (pid,))
        p = cur.fetchone(); con.close()
        return p

    def add(self):
        if PatientEditDialog(None, self).exec() == QDialog.DialogCode.Accepted: self.load()
    def edit(self):
        p = self.get_patient()
        if p: PatientHubDialog(p, "edit", self).exec(); self.load()
    def view(self):
        p = self.get_patient()
        if p: PatientHubDialog(p, "view", self).exec()

if __name__ == "__main__":
    init_db()
    app = QApplication(sys.argv)
    w = App()
    w.show()
    sys.exit(app.exec())