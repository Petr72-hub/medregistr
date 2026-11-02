"""Shared desktop theme: a quiet frame, white working sheet, explicit status colours."""
from pathlib import Path
from PyQt6.QtGui import QFont, QFontDatabase

STYLE_SHEET = """
QWidget { font-family: 'Segoe UI'; font-size: 14px; color: #1b302e; }
QWidget#applicationFrame, QDialog { background: #f1f5f3; }
QFrame#workSheet, QWidget#workSheet { background: #ffffff; border: 1px solid #e0e8e4; border-radius: 12px; }
QLabel { background: transparent; border: none; }
QLabel[role='title'] { font-size: 24px; font-weight: 600; color: #173f39; }
QLabel[role='brand'] { font-size: 21px; font-weight: 600; color: #174f45; }
QLabel[role='section'] { font-size: 16px; font-weight: 600; }
QLabel[role='muted'] { color: #536762; font-size: 12px; }
QLabel[role='metric'] { font-size: 28px; font-weight: 600; }
QLabel[role='notice'] { color: #795217; background: #fff7e7; padding: 10px; border-radius: 6px; }
QLabel[role='danger'] { color: #a52e36; }
QLineEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox, QTextEdit {
    background: #ffffff; border: 1px solid #bccbc5; border-radius: 6px;
    padding: 7px 10px; min-height: 20px; selection-background-color: #d9ebe4;
    selection-color: #173f39;
}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus {
    border: 2px solid #287969; padding: 6px 9px;
}
QLineEdit:disabled, QComboBox:disabled, QDateEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    background: #edf1ef; color: #536762; border-color: #d5ddd9;
}
QComboBox::drop-down, QDateEdit::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: white; selection-background-color: #e3ece7; selection-color: #173f39; padding: 5px; }
QPushButton { background: #ffffff; border: 1px solid #bfccc6; border-radius: 6px; padding: 8px 14px; min-height: 18px; }
QPushButton:hover { background: #e8efeb; border-color: #8fa99d; }
QPushButton:pressed { background: #d5e4dc; }
QPushButton:focus { border: 2px solid #287969; padding: 7px 13px; }
QPushButton[role='primary'] { background: #173f39; color: #ffffff; border-color: #173f39; font-weight: 600; }
QPushButton[role='primary']:hover { background: #24594e; }
QPushButton[role='nav'] { text-align: left; background: transparent; border: 1px solid transparent; padding: 10px 12px; }
QPushButton[role='nav']:hover, QPushButton[role='nav']:checked { background: #e1e9e4; border-color: #e1e9e4; }
QPushButton[role='danger'] { color: #a52e36; }
QPushButton:disabled { background: #edf1ef; color: #63756c; border-color: #d8e0db; }
QTableWidget, QTableView { background: white; border: 1px solid #e0e8e4; border-radius: 6px; gridline-color: #edf1ef; selection-background-color: #e0eee7; selection-color: #173f39; }
QTableWidget::item { padding: 8px; border-bottom: 1px solid #edf1ef; }
QHeaderView::section { background: #ffffff; color: #536762; padding: 9px 8px; border: none; border-bottom: 1px solid #dce5df; font-weight: 500; }
QTableCornerButton::section { background: white; border: none; }
QTabWidget::pane { border: 1px solid #e0e8e4; background: white; border-radius: 8px; top: -1px; }
QTabBar::tab { background: transparent; color: #536762; padding: 12px 18px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #173f39; border-bottom: 2px solid #287969; }
QGroupBox { background: white; border: 1px solid #e0e8e4; border-radius: 8px; margin-top: 22px; padding: 16px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; color: #173f39; font-weight: 600; }
QCheckBox { spacing: 8px; min-height: 25px; }
QCheckBox::indicator { width: 16px; height: 16px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { width: 10px; background: #f1f5f3; }
QScrollBar::handle:vertical { background: #bbc9c0; min-height: 24px; border-radius: 4px; }
QToolTip { background: #173f39; color: white; border: none; padding: 6px; }
"""


def configure_app(app):
    # Explicit font loading also gives Cyrillic glyphs to offscreen Windows previews.
    font_path = Path('C:/Windows/Fonts/segoeui.ttf')
    if font_path.exists():
        QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont('Segoe UI', 10))
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE_SHEET)
