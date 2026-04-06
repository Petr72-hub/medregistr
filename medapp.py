# =========================
# MEDICAL SYSTEM PRO FINAL
# =========================
import sys, os, shutil, sqlite3
from datetime import datetime
from PyQt6.QtWidgets import *
from PyQt6.QtCore import Qt

DB_NAME = "children.db"
FILES_DIR = "files"
if not os.path.exists(FILES_DIR): 
    os.makedirs(FILES_DIR)

MKB_CODES = [
    "K29 Гастрит", "K21 ГЭРБ", "K52 Гастроэнтерит", "K25 Язва желудка", 
    "K26 Язва 12ПК", "K58 СРК", "E11 Диабет", "B15 Гепатит A"
]

ANALYSES = [
    "ОАК", "ОАМ", "Биохимия", "ФГДС", "УЗИ ОБП", "Копрология", "Скрытая кровь",
    "ХВЗК", "БАК посев", "ИХА Helicobacter", "Колоноскопия", "КТ ОБП", "РГ ОБП"
]

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
        complaints TEXT, life TEXT, disease TEXT)
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS analyses(
        id INTEGER PRIMARY KEY,
        pid INTEGER, name TEXT, result TEXT, file TEXT)
    """)
    con.commit()
    con.close()

# =========================
# SMART INPUT (LIKE GOOGLE)
# =========================
class SmartCombo(QComboBox):
    def __init__(self, data, parent=None):
        super().__init__(parent)
        self.data = data
        self.setEditable(True)
        self.addItems(data)
        self.setPlaceholderText("Выберите анализ...")
        self.lineEdit().textEdited.connect(self.filter)
        
    def filter(self, text):
        self.blockSignals(True)
        self.clear()
        res = [x for x in self.data if text.lower() in x.lower()][:5]
        self.addItems(res)
        self.setEditText(text)
        self.blockSignals(False)

# =========================
# VIEW CARD
# =========================
class View(QDialog):
    def __init__(self, p):
        super().__init__()
        self.setWindowTitle("Карточка пациента")
        self.resize(900, 700)
        
        layout = QVBoxLayout()

        def block(title, content):
            box = QGroupBox(title)
            v = QVBoxLayout()
            lbl = QLabel(content)
            lbl.setWordWrap(True)
            v.addWidget(lbl)
            box.setLayout(v)
            return box

        fio = f"{p[1]} {p[2]} {p[3]}"
        layout.addWidget(block("Паспортная часть", f"ФИО: {fio}\nДата: {p[4]}\nМКБ: {p[5]}"))
        layout.addWidget(block("Жалобы", p[6]))
        layout.addWidget(block("Анамнез жизни", p[7]))
        layout.addWidget(block("Анамнез заболевания", p[8]))

        # analyses
        table = QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["Анализ", "Результат", "Файл"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT name,result,file FROM analyses WHERE pid=?", (p[0],))
        data = cur.fetchall()
        con.close()

        table.setRowCount(len(data))
        for r, row in enumerate(data):
            for c, val in enumerate(row):
                table.setItem(r, c, QTableWidgetItem(str(val if val else "-")))

        layout.addWidget(table)
        self.setLayout(layout)

# =========================
# EDIT
# =========================
class Edit(QDialog):
    def __init__(self, p=None):
        super().__init__()
        self.p = p
        self.setWindowTitle("Редактирование пациента")
        self.resize(900, 700)
        
        l = QVBoxLayout()

        fio = QHBoxLayout()
        self.last = QLineEdit()
        self.last.setPlaceholderText("Фамилия")
        self.first = QLineEdit()
        self.first.setPlaceholderText("Имя")
        self.middle = QLineEdit()
        self.middle.setPlaceholderText("Отчество")
        fio.addWidget(self.last)
        fio.addWidget(self.first)
        fio.addWidget(self.middle)
        l.addLayout(fio)

        self.birth = QLineEdit()
        self.birth.setPlaceholderText("ДДММГГГГ")
        self.birth.textChanged.connect(self.format_date)
        l.addWidget(self.birth)

        self.mkb = SmartCombo(MKB_CODES)
        l.addWidget(self.mkb)

        self.compl = QTextEdit()
        self.compl.setPlaceholderText("Жалобы")
        self.compl.setMaximumHeight(80)
        
        self.life = QTextEdit()
        self.life.setPlaceholderText("Анамнез жизни")
        self.life.setMaximumHeight(80)
        
        self.dis = QTextEdit()
        self.dis.setPlaceholderText("Анамнез заболевания")
        self.dis.setMaximumHeight(80)

        l.addWidget(self.compl)
        l.addWidget(self.life)
        l.addWidget(self.dis)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Анализ", "Результат", "Файл"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        l.addWidget(self.table)

        btn = QPushButton("Добавить анализ")
        btn.clicked.connect(self.add_row)
        l.addWidget(btn)

        save = QPushButton("Сохранить")
        save.clicked.connect(self.save)
        l.addWidget(save)

        self.setLayout(l)

        if p:
            self.last.setText(p[1])
            self.first.setText(p[2])
            self.middle.setText(p[3])
            self.birth.setText(p[4])
            self.mkb.setCurrentText(p[5])
            self.compl.setPlainText(p[6])
            self.life.setPlainText(p[7])
            self.dis.setPlainText(p[8])

    def format_date(self):
        t = self.birth.text().replace(".", "")
        if len(t) > 8:
            t = t[:8]
        if len(t) >= 4 and t.isdigit():
            self.birth.blockSignals(True)
            self.birth.setText(f"{t[:2]}.{t[2:4]}.{t[4:]}")
            self.birth.blockSignals(False)

    def add_row(self):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setRowHeight(r, 38)  # Увеличенная высота строки
        
        # Создаем ComboBox
        combo = SmartCombo(ANALYSES)
        combo.setCurrentIndex(-1)
        self.table.setCellWidget(r, 0, combo)
        
        # Поле результата
        self.table.setItem(r, 1, QTableWidgetItem(""))
        
        # Кнопка файла
        b = QPushButton("📎")
        b.setFixedWidth(50)
        b.clicked.connect(lambda checked, row=r: self.attach(row))
        self.table.setCellWidget(r, 2, b)
        
        # Устанавливаем фокус
        self.table.setCurrentCell(r, 0)
        combo.show()
        combo.setFocus()

    def attach(self, row):
        path = QFileDialog.getOpenFileName(self, "Выберите файл")[0]
        if path:
            name = os.path.basename(path)
            dest = os.path.join(FILES_DIR, name)
            shutil.copy(path, dest)
            self.table.setItem(row, 2, QTableWidgetItem(dest))

    def save(self):
        if not self.last.text().strip() or not self.first.text().strip():
            QMessageBox.warning(self, "Внимание", "Заполните фамилию и имя!")
            return

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()

        data = (
            self.last.text(),
            self.first.text(),
            self.middle.text(),
            self.birth.text(),
            self.mkb.currentText(),
            self.compl.toPlainText(),
            self.life.toPlainText(),
            self.dis.toPlainText()
        )

        try:
            if self.p:
                cur.execute(
                    "UPDATE patients SET last=?,first=?,middle=?,birth=?,mkb=?,complaints=?,life=?,disease=? WHERE id=?",
                    (*data, self.p[0])
                )
                pid = self.p[0]
            else:
                cur.execute(
                    "INSERT INTO patients(last,first,middle,birth,mkb,complaints,life,disease) VALUES(?,?,?,?,?,?,?,?)",
                    data
                )
                pid = cur.lastrowid

            cur.execute("DELETE FROM analyses WHERE pid=?", (pid,))

            for r in range(self.table.rowCount()):
                combo = self.table.cellWidget(r, 0)
                name = combo.currentText().strip() if combo else ""
                
                if not name:
                    continue
                
                res_item = self.table.item(r, 1)
                res = res_item.text() if res_item else ""
                
                file_item = self.table.item(r, 2)
                file = file_item.text() if file_item else ""
                
                cur.execute("INSERT INTO analyses(pid,name,result,file) VALUES(?,?,?,?)",
                          (pid, name, res, file))

            con.commit()
            QMessageBox.information(self, "Успех", "Данные сохранены!")
            self.accept()
        except Exception as e:
            con.rollback()
            QMessageBox.critical(self, "Ошибка", str(e))
        finally:
            con.close()

# =========================
# MAIN
# =========================
class App(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Медицинская система")
        self.resize(1000, 600)
        
        l = QVBoxLayout()

        self.t = QTableWidget()
        self.t.setColumnCount(4)
        self.t.setHorizontalHeaderLabels(["ID", "Фамилия", "Имя", "МКБ"])
        self.t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        l.addWidget(self.t)

        h = QHBoxLayout()
        add = QPushButton("Добавить")
        add.clicked.connect(self.add)
        edit = QPushButton("Редактировать")
        edit.clicked.connect(self.edit)
        view = QPushButton("Просмотр")
        view.clicked.connect(self.view)
        h.addWidget(add)
        h.addWidget(edit)
        h.addWidget(view)
        l.addLayout(h)

        self.setLayout(l)
        self.load()

    def load(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT id,last,first,mkb FROM patients")
        d = cur.fetchall()
        con.close()

        self.t.setRowCount(len(d))
        for r, row in enumerate(d):
            for c, val in enumerate(row):
                self.t.setItem(r, c, QTableWidgetItem(str(val)))

    def get(self):
        r = self.t.currentRow()
        if r == -1:
            return None
        id = self.t.item(r, 0).text()
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT * FROM patients WHERE id=?", (id,))
        p = cur.fetchone()
        con.close()
        return p

    def add(self):
        if Edit().exec() == 1:
            self.load()

    def edit(self):
        p = self.get()
        if p and Edit(p).exec() == 1:
            self.load() 

    def view(self):
        p = self.get()
        if p:
            View(p).exec()

# =========================
# RUN
# =========================
if __name__ == "__main__":
    init_db()
    app = QApplication(sys.argv)
    w = App()
    w.show()
    sys.exit(app.exec())