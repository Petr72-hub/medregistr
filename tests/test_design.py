import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt6.QtWidgets import QApplication,QPushButton,QCalendarWidget
from PyQt6.QtCore import QEvent,Qt
from PyQt6.QtTest import QTest
import design


def test_theme_switch_calendar_and_hover(monkeypatch):
    app=QApplication.instance() or QApplication([])
    class Settings:
        def __init__(self,*args): pass
        def value(self,*args): return 'light'
        def setValue(self,*args): pass
    monkeypatch.setattr(design,'QSettings',Settings)
    design.configure_app(app)
    button=QPushButton('Склад');button.show()
    calendar=QCalendarWidget();calendar.show();app.processEvents()
    assert calendar.weekdayTextFormat(Qt.DayOfWeek.Saturday).foreground().color().name() == '#1b302e'
    design.toggle_theme(app)
    assert app.property('theme') == 'dark'
    assert calendar.weekdayTextFormat(Qt.DayOfWeek.Sunday).foreground().color().name() == '#e2ebe6'
    app.sendEvent(button,QEvent(QEvent.Type.Enter));QTest.qWait(180)
    assert 'background-color' in button.styleSheet()
    app.sendEvent(button,QEvent(QEvent.Type.Leave));QTest.qWait(180)
    design.toggle_theme(app)
    assert app.property('theme') == 'light'
    button.hide();calendar.hide()
    app.removeEventFilter(app._theme_effects)
