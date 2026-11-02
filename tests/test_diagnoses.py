import sqlite3
from contextlib import closing
import pytest
from diagnoses import MKB_CODES, therapy_diagnosis
from test_medication_ui import sandbox, app
import medication_ui as ui
import bot

@pytest.mark.parametrize('diagnosis,key', [('K50 Болезнь Крона','Crohn'),('K50.1 Болезнь Крона толстой кишки','Crohn'),('K51.9 Колит','UC'),('K29 Гастрит','K29 Гастрит'),('K500 Ошибка','K500 Ошибка'),('', 'unspecified')])
def test_diagnosis_mapping(diagnosis,key):
    label, actual = therapy_diagnosis(diagnosis)
    assert actual == key
    assert label

@pytest.mark.parametrize('diagnosis,key', [('K50 Болезнь Крона','Crohn'),('K51 Язвенный колит','UC'),('K59.0 Запор','K59.0 Запор'),('E11 Диабет','E11 Диабет')])
def test_passport_diagnosis_in_planner(sandbox,diagnosis,key):
    path,_=sandbox
    with closing(sqlite3.connect(path)) as con:
        con.execute('UPDATE patients SET mkb=? WHERE id=1',(diagnosis,)); con.commit()
    planner=ui.TherapyPlanner(path,1)
    assert planner.diagnosis_box.currentText() == diagnosis
    assert planner.diagnosis_box.currentData() == key
    assert not planner.diagnosis_box.isEnabled()
    planner.drug_box.setCurrentText('Ведолизумаб')
    assert planner.diagnosis_box.currentText() == diagnosis


def test_catalog_and_legacy_passport(sandbox):
    path,_=sandbox
    codes={x.split()[0] for x in MKB_CODES}
    assert {'K21','P78.8','K29','K25','K26','K31.8','K50','K51','K52.2','K52.9','K58','K59.0','K59.1','K90.0','K90.1','K90.4','K73','K73.2','K76.0','K80','K82.8','K85','K86','A08','A08.0','A08.1','A04','A09','Q40.0','Q43','Q43.1','E84.1','P77'} <= codes
    with closing(sqlite3.connect(path)) as con:
        con.execute("UPDATE patients SET mkb='E11 Диабет' WHERE id=1");con.commit()
        p=con.execute('SELECT '+bot.PATIENT_COLUMNS+' FROM patients WHERE id=1').fetchone()
    form=bot.PatientForm(p)
    assert form.mkb.currentText() == 'E11 Диабет'
    assert form.mkb.findText('K50 Болезнь Крона') >= 0
