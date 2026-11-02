"""Shared desktop theme: a quiet frame, white working sheet, explicit status colours."""
from pathlib import Path
from PyQt6.QtGui import QFont, QFontDatabase, QPalette, QColor, QTextCharFormat, QIcon
from PyQt6.QtCore import QObject, QEvent, QVariantAnimation, QSettings, Qt, QTimer
from PyQt6.QtWidgets import QPushButton, QCalendarWidget, QToolButton
from PyQt6 import sip

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
    app._theme_effects = ThemeEffects(app)
    app.installEventFilter(app._theme_effects)
    apply_theme(app, QSettings('MedRegister', 'Desktop').value('theme', 'light'))


DARK_COLORS = {
    '#f1f5f3': '#181c1b', '#ffffff': '#222826', '#1b302e': '#e2ebe6',
    '#173f39': '#d7e9e0', '#174f45': '#9ed4bd', '#536762': '#b1c2b9',
    '#287969': '#88c8ad', '#e0e8e4': '#3b4841', '#bccbc5': '#596a60',
    '#d9ebe4': '#36594a', '#edf1ef': '#2b3430', '#d5ddd9': '#46544b',
    '#e3ece7': '#34483c', '#bfccc6': '#58685f', '#e8efeb': '#35443c',
    '#8fa99d': '#88ad99', '#d5e4dc': '#40584a', '#24594e': '#416b57',
    '#e1e9e4': '#34433b', '#a52e36': '#f1a4a7', '#63756c': '#b3c0b8',
    '#d8e0db': '#47554c', '#e0eee7': '#365746', '#dce5df': '#45554a',
    '#bbc9c0': '#52665a', '#795217': '#edd39e', '#fff7e7': '#3c3424',
}


def apply_theme(app, theme):
    theme = 'dark' if theme == 'dark' else 'light'
    app.setProperty('theme', theme)
    dark = theme == 'dark'
    sheet = STYLE_SHEET
    if dark:
        import re
        sheet = re.sub(r'#[0-9a-fA-F]{6}', lambda m: DARK_COLORS.get(m.group(0).lower(), m.group(0)), sheet)
        # A pale primary fill needs dark ink in the dark theme.
        sheet = sheet.replace('background: white', 'background: #222826')
        sheet += "QPushButton[role='primary'] { background: #9ed4bd; color: #14251c; border-color: #9ed4bd; }"
        sheet += "QPushButton[role='primary']:hover { background: #b5e0cc; color: #14251c; }"
    ground, surface, ink, muted = ('#181c1b','#222826','#e2ebe6','#b1c2b9') if dark else ('#f1f5f3','#ffffff','#1b302e','#536762')
    palette = QPalette()
    for role, color in [(QPalette.ColorRole.Window,ground), (QPalette.ColorRole.Base,surface),
                        (QPalette.ColorRole.AlternateBase,surface), (QPalette.ColorRole.WindowText,ink),
                        (QPalette.ColorRole.Text,ink), (QPalette.ColorRole.Button,surface),
                        (QPalette.ColorRole.ButtonText,ink), (QPalette.ColorRole.PlaceholderText,muted),
                        (QPalette.ColorRole.Highlight,'#365746' if dark else '#d9ebe4'),
                        (QPalette.ColorRole.HighlightedText,ink), (QPalette.ColorRole.ToolTipBase,surface),
                        (QPalette.ColorRole.ToolTipText,ink), (QPalette.ColorRole.Link,'#9ed4bd' if dark else '#174f45')]:
        palette.setColor(role,QColor(color))
    palette.setColor(QPalette.ColorGroup.Disabled,QPalette.ColorRole.Text,QColor(muted))
    palette.setColor(QPalette.ColorGroup.Disabled,QPalette.ColorRole.ButtonText,QColor(muted))
    app.setPalette(palette)
    sheet += f"""
QAbstractItemView {{ color: {ink}; background: {surface}; alternate-background-color: {surface}; }}
QCalendarWidget QWidget {{ background: {surface}; color: {ink}; }}
QCalendarWidget QToolButton {{ background: {ground}; color: {ink}; border: none; padding: 5px; }}
QCalendarWidget QMenu {{ background: {surface}; color: {ink}; }}
QCalendarWidget QSpinBox {{ background: {surface}; color: {ink}; }}
QMenu {{ background: {surface}; color: {ink}; border: 1px solid {'#596a60' if dark else '#bccbc5'}; }}
QMenu::item:selected {{ background: {'#365746' if dark else '#d9ebe4'}; }}
"""
    app.setStyleSheet(sheet)
    for widget in app.allWidgets():
        if isinstance(widget,QCalendarWidget):
            style_calendar(widget, dark)
        if isinstance(widget,QPushButton) and widget.property('animatedHover'):
            widget.setStyleSheet('')
    QSettings('MedRegister', 'Desktop').setValue('theme',theme)


def toggle_theme(app):
    apply_theme(app, 'light' if app.property('theme') == 'dark' else 'dark')


def style_calendar(widget, dark):
    if sip.isdeleted(widget):
        return
    widget.setMinimumWidth(320)
    widget.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
    for name, text in [('qt_calendar_prevmonth', '‹'), ('qt_calendar_nextmonth', '›')]:
        button = widget.findChild(QToolButton, name)
        if button:
            button.setIcon(QIcon())
            button.setText(text)
    fmt = QTextCharFormat()
    fmt.setForeground(QColor('#e2ebe6' if dark else '#1b302e'))
    for day in (Qt.DayOfWeek.Monday, Qt.DayOfWeek.Tuesday, Qt.DayOfWeek.Wednesday,
                Qt.DayOfWeek.Thursday, Qt.DayOfWeek.Friday, Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
        widget.setWeekdayTextFormat(day,fmt)


class ThemeEffects(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.animations = {}

    def eventFilter(self, widget, event):
        if isinstance(widget,QCalendarWidget) and event.type() == QEvent.Type.Show:
            QTimer.singleShot(0, lambda: style_calendar(widget,self.app.property('theme') == 'dark'))
        if isinstance(widget,QPushButton) and widget.isEnabled() and event.type() in (QEvent.Type.Enter,QEvent.Type.Leave):
            dark = self.app.property('theme') == 'dark'
            primary = widget.property('role') == 'primary'
            base = ('#9ed4bd' if dark else '#173f39') if primary else ('#222826' if dark else '#ffffff')
            over = ('#b5e0cc' if dark else '#24594e') if primary else ('#35443c' if dark else '#e8efeb')
            current = widget.palette().color(QPalette.ColorRole.Button)
            animation = self.animations.get(widget)
            if animation:
                current = animation.currentValue() or current
                animation.stop()
            animation = QVariantAnimation(widget)
            animation.setDuration(150)
            animation.setStartValue(current)
            animation.setEndValue(QColor(over if event.type() == QEvent.Type.Enter else base))
            widget.setProperty('animatedHover',True)
            animation.valueChanged.connect(lambda color, button=widget: button.setStyleSheet("background-color: " + color.name() + ";"))
            self.animations[widget] = animation
            animation.start()
        return super().eventFilter(widget,event)
