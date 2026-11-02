"""Local physician-selected schedules and a dated stock ledger.

Rules implement the project's agreed local scope, not a prescribing guideline.
Only an explicit issue consumes stock; pending events are reservations.
"""
from __future__ import annotations

from calendar import monthrange
from contextlib import closing, contextmanager
from datetime import date as Date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


DRUG_UNITS = {'Инфликсимаб': 100, 'Ведолизумаб': 300, 'Адалимумаб': 40}
ALIASES = {'infliximab': 'Инфликсимаб', 'vedolizumab': 'Ведолизумаб',
           'adalimumab': 'Адалимумаб'}


def _drug(value):
    value = ALIASES.get(str(value).lower(), value)
    if value not in DRUG_UNITS:
        raise ValueError('Неизвестный препарат')
    return value


def _date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, Date):
        return value
    for fmt in ('%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(str(value), fmt).date()
        except ValueError:
            pass
    raise ValueError('Дата должна быть ГГГГ-ММ-ДД или ДД.ММ.ГГГГ')


def _decimal(value):
    try:
        number = Decimal(str(value).replace(',', '.'))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError('Требуется положительное число') from None
    if not number.is_finite() or number <= 0:
        raise ValueError('Требуется положительное конечное число')
    return number


def _integer(value, allow_zero=False):
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Требуется целое количество') from None
    if not number.is_finite() or number != number.to_integral_value() or number < (0 if allow_zero else 1):
        raise ValueError('Требуется целое неотрицательное количество' if allow_zero else 'Требуется целое положительное количество')
    return int(number)


def units_for_dose(dose_mg, unit_mg):
    return int((_decimal(dose_mg) / _decimal(unit_mg)).to_integral_value(rounding=ROUND_CEILING))


def _crohn(diagnosis):
    text = str(diagnosis).lower()
    return any(token in text for token in ('crohn', 'крон', 'k50', 'к50'))


def recalculate_event(event, weight_kg):
    """Return a recalculated copy; manual doses remain physician-entered."""
    result = dict(event)
    drug = _drug(event['drug'])
    weight = _decimal(weight_kg)
    phase = event.get('phase', 'maintenance')
    if phase == 'manual':
        dose = _decimal(event['dose_mg'])
    elif drug == 'Инфликсимаб':
        choice = Decimal(5) if phase == 'induction' else _decimal(event.get('dose_choice') or 5)
        if choice not in (Decimal(5), Decimal(10)):
            raise ValueError('Инфликсимаб: выберите 5 или 10 мг/кг')
        dose = weight * choice
    elif drug == 'Ведолизумаб':
        choice = Decimal(5 if weight >= 30 else 10) if phase == 'induction' else _decimal(event.get('dose_choice'))
        if choice not in (Decimal(5), Decimal(10)):
            raise ValueError('Ведолизумаб: выберите 5 или 10 мг/кг')
        dose = weight * choice
    elif phase == 'induction':
        step = int(event.get('induction_step') or 0)
        if step not in (0, 1):
            raise ValueError('Некорректный шаг индукции')
        dose = Decimal((160 if weight >= 40 else 80) if step == 0 else (80 if weight >= 40 else 40))
    else:
        if not _crohn(event.get('diagnosis', '')):
            raise ValueError('Поддержка адалимумабом при ЯК не включена: ожидается протокол')
        dose = _decimal(event.get('dose_choice'))
        if dose not in (Decimal(20), Decimal(40)):
            raise ValueError('Адалимумаб: выберите фиксированную дозу 20 или 40 мг')
    result.update(drug=drug, weight_kg=str(weight), dose_mg=str(dose),
                  unit_mg=DRUG_UNITS[drug], units=units_for_dose(dose, DRUG_UNITS[drug]))
    return result


def preview_schedule(drug, start_date, weight_kg, phase='induction', dose_choice=None,
                     interval_weeks=None, maintenance_start=None, diagnosis='Crohn', through_date=None):
    drug = _drug(drug)
    start = _date(start_date)
    anniversary = Date(start.year + 1, start.month, min(start.day, monthrange(start.year + 1, start.month)[1]))
    through = _date(through_date) if through_date is not None else anniversary
    if through < start:
        raise ValueError('Конец периода раньше начала')
    if phase not in ('induction', 'maintenance'):
        raise ValueError('Для дополнительной записи используйте manual_event')
    interval = _integer(interval_weeks if interval_weeks is not None else (2 if drug == 'Адалимумаб' else 8))
    allowed = (1, 2) if drug == 'Адалимумаб' else (4, 8) if drug == 'Ведолизумаб' else (4, 6, 8)
    if interval not in allowed:
        raise ValueError('Интервал не входит в согласованную схему')
    support_allowed = drug != 'Адалимумаб' or _crohn(diagnosis)
    if phase == 'maintenance' and not support_allowed:
        raise ValueError('Поддержка адалимумабом при ЯК не включена: ожидается протокол')
    choice = dose_choice if dose_choice is not None else (5 if drug == 'Инфликсимаб' else None)
    offsets = (0, 14) if drug == 'Адалимумаб' else (0, 14, 42)
    support_start = (_date(maintenance_start) if maintenance_start else
                     start + timedelta(days=offsets[-1] + interval * 7) if phase == 'induction' else start)
    if phase == 'induction' and support_start <= start + timedelta(days=offsets[-1]):
        raise ValueError('Поддержка должна начаться после последней индукции')
    key = hashlib.sha256(f'{drug}|{start.isoformat()}|{phase}'.encode()).hexdigest()[:24]
    events = []

    def append_event(day, event_phase, step=None):
        event = {'plan_key': key, 'event_key': f'{event_phase}:{step if step is not None else day.isoformat()}',
                 'drug': drug, 'date': day.isoformat(), 'phase': event_phase,
                 'dose_choice': str(choice) if choice is not None else None,
                 'interval_weeks': interval, 'diagnosis': diagnosis,
                 'status': 'planned', 'issued_on': None, 'extra': False,
                 'induction_step': step}
        events.append(recalculate_event(event, weight_kg))

    if phase == 'induction':
        for index, offset in enumerate(offsets):
            day = start + timedelta(days=offset)
            if day <= through:
                append_event(day, 'induction', index)
    if support_allowed:
        day = support_start
        while day <= through:
            append_event(day, 'maintenance')
            day += timedelta(weeks=interval)
    return events


def manual_event(drug, date, dose_mg, weight_kg=None, extra=False):
    drug = _drug(drug)
    dose = _decimal(dose_mg)
    return {'plan_key': 'manual:' + uuid4().hex, 'event_key': 'manual', 'drug': drug,
            'date': _date(date).isoformat(), 'weight_kg': str(_decimal(weight_kg)) if weight_kg is not None else None,
            'dose_mg': str(dose), 'unit_mg': DRUG_UNITS[drug], 'units': units_for_dose(dose, DRUG_UNITS[drug]),
            'phase': 'manual', 'dose_choice': None, 'interval_weeks': 0, 'diagnosis': '',
            'status': 'planned', 'issued_on': None, 'extra': bool(extra), 'induction_step': None}


SCHEMA = {
    'stock_receipts': '''CREATE TABLE stock_receipts(id INTEGER PRIMARY KEY,date TEXT,drug TEXT,
        vials_count INTEGER,vial_mg REAL,comment TEXT)''',
    'medication_events': '''CREATE TABLE medication_events(
        id INTEGER PRIMARY KEY, pid INTEGER NOT NULL, plan_key TEXT NOT NULL, event_key TEXT NOT NULL,
        drug TEXT NOT NULL, date TEXT NOT NULL, weight_kg TEXT, dose_mg TEXT NOT NULL,
        unit_mg INTEGER NOT NULL, units INTEGER NOT NULL CHECK(units>0), phase TEXT NOT NULL,
        dose_choice TEXT, interval_weeks INTEGER NOT NULL, diagnosis TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','issued','cancelled')),
        issued_on TEXT, extra INTEGER NOT NULL DEFAULT 0, induction_step INTEGER,
        rescheduled INTEGER NOT NULL DEFAULT 0, UNIQUE(pid,plan_key,event_key))''',
    'medication_issues': '''CREATE TABLE medication_issues(
        id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL UNIQUE, pid INTEGER NOT NULL,
        drug TEXT NOT NULL, issued_on TEXT NOT NULL, units INTEGER NOT NULL CHECK(units>0),
        dose_mg TEXT NOT NULL, weight_kg TEXT)''',
    'medication_safety': '''CREATE TABLE medication_safety(drug TEXT PRIMARY KEY,
        units INTEGER NOT NULL CHECK(units>=0))''',
    'medication_weights': '''CREATE TABLE medication_weights(id INTEGER PRIMARY KEY,
        pid INTEGER NOT NULL,effective_date TEXT NOT NULL,weight_kg TEXT NOT NULL,
        UNIQUE(pid,effective_date))''',
}


def initialize_medication(db_path):
    """Add new tables only; back up the existing DB before schema changes."""
    path = Path(db_path)
    if not path.exists():
        raise ValueError('Сначала создайте базу пациентов')
    with closing(sqlite3.connect(path)) as con:
        if con.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise sqlite3.DatabaseError('База не прошла проверку целостности')
        existing = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = set(SCHEMA) - existing
        if not missing:
            return None
        if 'patients' not in existing:
            raise ValueError('Не найдена таблица пациентов')
        backup = path.with_name(path.name + '.backup-medication-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:8])
        with closing(sqlite3.connect(backup)) as target:
            con.backup(target)
        try:
            con.execute('BEGIN IMMEDIATE')
            for name in SCHEMA:
                if name in missing:
                    con.execute(SCHEMA[name])
            con.execute('CREATE INDEX IF NOT EXISTS medication_events_due ON medication_events(drug,status,date)')
            con.commit()
        except Exception:
            con.rollback()
            raise
        return backup


@contextmanager
def _connection(db_path, write=False):
    mode = 'rw' if write else 'ro'
    con = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=' + mode, uri=True, timeout=15)
    con.row_factory = sqlite3.Row
    try:
        if write:
            con.execute('BEGIN IMMEDIATE')
        yield con
        if write:
            con.commit()
    except Exception:
        if write:
            con.rollback()
        raise
    finally:
        con.close()


def _patient(con, pid):
    if not con.execute('SELECT 1 FROM patients WHERE id=?', (pid,)).fetchone():
        raise ValueError('Пациент не найден')


def _owned_event(con, pid, event_id):
    _patient(con, pid)
    row = con.execute('SELECT * FROM medication_events WHERE id=? AND pid=?', (event_id, pid)).fetchone()
    if row is None:
        raise ValueError('Событие не найдено у выбранного пациента')
    return dict(row)


EVENT_FIELDS = ('pid', 'plan_key', 'event_key', 'drug', 'date', 'weight_kg', 'dose_mg', 'unit_mg',
                'units', 'phase', 'dose_choice', 'interval_weeks', 'diagnosis', 'extra', 'induction_step')


def _save_plan(con, pid, events, reactivate=False):
    _patient(con, pid)
    ids = []
    for source in events:
        event = dict(source)
        event['pid'] = pid
        event['drug'] = _drug(event['drug'])
        event['date'] = _date(event['date']).isoformat()
        event['dose_mg'] = str(_decimal(event['dose_mg']))
        event['unit_mg'] = DRUG_UNITS[event['drug']]
        event['units'] = units_for_dose(event['dose_mg'], event['unit_mg'])
        event['extra'] = bool(event.get('extra'))
        event.setdefault('diagnosis', '')
        event.setdefault('dose_choice', None)
        event.setdefault('induction_step', None)
        event.setdefault('weight_kg', None)
        if event['phase'] not in ('induction', 'maintenance', 'manual'):
            raise ValueError('Неизвестная фаза лечения')
        event['interval_weeks'] = _integer(event['interval_weeks'], allow_zero=event['phase'] == 'manual')
        if event['weight_kg'] is not None:
            event['weight_kg'] = str(_decimal(event['weight_kg']))
        if event['phase'] != 'manual':
            event = recalculate_event(event, event['weight_kg'])
        existing = con.execute('SELECT * FROM medication_events WHERE pid=? AND plan_key=? AND event_key=?',
                               (pid, event['plan_key'], event['event_key'])).fetchone()
        if existing and (existing['status'] == 'issued' or (existing['rescheduled'] and not reactivate) or
                         (existing['status'] == 'cancelled' and not reactivate)):
            ids.append(existing['id'])
            continue
        duplicate = con.execute("SELECT id,status FROM medication_events WHERE pid=? AND drug=? AND date=? AND status!='cancelled' AND id!=?",
                                (pid, event['drug'], event['date'], existing['id'] if existing else -1)).fetchone()
        if duplicate and not event['extra']:
            if duplicate['status'] == 'issued':
                ids.append(duplicate['id'])
                continue
            raise ValueError('На эту дату уже есть план препарата; используйте замену будущей схемы')
        values = [event[field] for field in EVENT_FIELDS]
        if existing:
            con.execute('UPDATE medication_events SET ' + ','.join(field + '=?' for field in EVENT_FIELDS) +
                        ",status='planned',issued_on=NULL,rescheduled=0 WHERE id=?", values + [existing['id']])
            ids.append(existing['id'])
        else:
            cursor = con.execute('INSERT INTO medication_events(' + ','.join(EVENT_FIELDS) + ') VALUES(' +
                                 ','.join('?' for _ in EVENT_FIELDS) + ')', values)
            ids.append(cursor.lastrowid)
    return ids


def save_plan(db_path, pid, events):
    with _connection(db_path, True) as con:
        return _save_plan(con, pid, events)


def replace_plan(db_path, pid, events, effective_date):
    events = list(events)
    effective = _date(effective_date).isoformat()
    if not events:
        raise ValueError('Новая схема пуста')
    if any(_date(e['date']).isoformat() < effective for e in events):
        raise ValueError('Новая схема содержит даты до начала замены')
    with _connection(db_path, True) as con:
        _patient(con, pid)
        for drug in {_drug(e['drug']) for e in events}:
            con.execute("UPDATE medication_events SET status='cancelled' WHERE pid=? AND drug=? AND date>=? AND status='planned'",
                        (pid, drug, effective))
        return _save_plan(con, pid, events, reactivate=True)


def list_events(db_path, pid=None):
    with _connection(db_path) as con:
        if pid is None:
            return [dict(r) for r in con.execute('SELECT * FROM medication_events ORDER BY date,id')]
        _patient(con, pid)
        return [dict(r) for r in con.execute('SELECT * FROM medication_events WHERE pid=? ORDER BY date,id', (pid,))]


def cancel_event(db_path, pid, event_id):
    with _connection(db_path, True) as con:
        event = _owned_event(con, pid, event_id)
        if event['status'] == 'issued':
            raise ValueError('Выданное событие нельзя отменить: сохраните историю выдачи')
        con.execute("UPDATE medication_events SET status='cancelled' WHERE id=?", (event_id,))


def list_receipts(db_path):
    with _connection(db_path) as con:
        return [dict(r, units=r['vials_count']) for r in con.execute('SELECT * FROM stock_receipts ORDER BY id DESC')]


def add_receipt(db_path, drug, date, units, comment=''):
    drug = _drug(drug)
    day = _date(date).isoformat()
    units = _integer(units)
    with _connection(db_path, True) as con:
        cursor = con.execute('INSERT INTO stock_receipts(date,drug,vials_count,vial_mg,comment) VALUES(?,?,?,?,?)',
                             (day, drug, units, DRUG_UNITS[drug], comment))
        return cursor.lastrowid


def set_safety_stock(db_path, drug, units):
    with _connection(db_path, True) as con:
        con.execute('INSERT INTO medication_safety(drug,units) VALUES(?,?) ON CONFLICT(drug) DO UPDATE SET units=excluded.units',
                    (_drug(drug), _integer(units, True)))


def _movements(con, drug):
    """Return dated actual/legacy movements, preserving old tables untouched."""
    movements = []
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'stock_receipts' in tables:
        for r in con.execute('SELECT id,date,drug,vials_count FROM stock_receipts'):
            if ALIASES.get(str(r['drug']).lower(), r['drug']) == drug:
                movements.append((_date(r['date']), _integer(r['vials_count'], True), 'receipt', r['id']))
    if 'therapy' in tables:
        for r in con.execute('SELECT id,date,drug,vials_count FROM therapy'):
            if ALIASES.get(str(r['drug']).lower(), r['drug']) == drug:
                movements.append((_date(r['date']), -_integer(r['vials_count'], True), 'legacy', r['id']))
    for r in con.execute('SELECT event_id,issued_on,units FROM medication_issues WHERE drug=?', (drug,)):
        movements.append((_date(r['issued_on']), -r['units'], 'issue', r['event_id']))
    return movements


def issue_event(db_path, pid, event_id, issued_on, today=None):
    today = _date(today) if today is not None else Date.today()
    issued = _date(issued_on)
    if issued > today:
        raise ValueError('Фактическая выдача не может быть датирована будущим')
    with _connection(db_path, True) as con:
        event = _owned_event(con, pid, event_id)
        if event['status'] == 'issued':
            return False
        if event['status'] != 'planned':
            raise ValueError('Отмененное событие нельзя выдать')
        movements = _movements(con, event['drug'])
        # Backdated issues must not consume stock already issued later.
        days = {issued, today} | {day for day, _, _, _ in movements if issued <= day <= today}
        if any(sum(qty for day, qty, _, _ in movements if day <= check) < event['units'] for check in days):
            raise ValueError('Недостаточно фактического остатка на дату выдачи или после нее')
        con.execute('INSERT INTO medication_issues(event_id,pid,drug,issued_on,units,dose_mg,weight_kg) VALUES(?,?,?,?,?,?,?)',
                    (event_id, pid, event['drug'], issued.isoformat(), event['units'], event['dose_mg'], event['weight_kg']))
        con.execute("UPDATE medication_events SET status='issued',issued_on=? WHERE id=?", (issued.isoformat(), event_id))
        return True


def forecast(db_path, target_date, today=None):
    today = _date(today) if today is not None else Date.today()
    target = _date(target_date)
    if target < today:
        raise ValueError('Дата прогноза раньше сегодняшней')
    result = {}
    with _connection(db_path) as con:
        for drug in DRUG_UNITS:
            movements = _movements(con, drug)
            current = sum(qty for day, qty, _, _ in movements if day <= today)
            safety_row = con.execute('SELECT units FROM medication_safety WHERE drug=?', (drug,)).fetchone()
            safety = safety_row[0] if safety_row else 0
            pending = [dict(r) for r in con.execute("SELECT date,units FROM medication_events WHERE drug=? AND status='planned'", (drug,))]
            due = [(max(today, _date(r['date'])), r['units']) for r in pending if _date(r['date']) <= target]
            future = [(day, qty, kind) for day, qty, kind, _ in movements if today < day <= target]
            days = sorted({today} | {d for d, _ in due} | {d for d, _, _ in future})
            balance = current
            minimum_free = current - safety
            first_shortage = today.isoformat() if minimum_free < 0 else None
            first_stockout = today.isoformat() if current < 0 else None
            timeline = []
            for day in days:
                receipts = sum(q for d, q, k in future if d == day and k == 'receipt')
                legacy_expense = -sum(q for d, q, k in future if d == day and k == 'legacy')
                expense = -sum(q for d, q, k in future if d == day and k != 'receipt')
                reserved = sum(q for d, q in due if d == day)
                balance += receipts - expense - reserved
                free = balance - safety
                if balance < 0 and first_stockout is None:
                    first_stockout = day.isoformat()
                if free < 0 and first_shortage is None:
                    first_shortage = day.isoformat()
                minimum_free = min(minimum_free, free)
                timeline.append({'date': day.isoformat(), 'receipt_units': receipts, 'reserved_units': reserved,
                                 'legacy_expense_units': legacy_expense, 'projected_balance': balance, 'free': free})
            future_legacy = sum(1 for d, _, kind, _ in movements if kind == 'legacy' and d > today)
            result[drug] = {'current_balance': current, 'reserve': sum(q for _, q in due), 'safety_stock': safety,
                            'projected_balance': balance, 'free': balance - safety, 'shortage': max(0, -minimum_free),
                            'first_shortage_date': first_shortage, 'first_stockout_date': first_stockout, 'timeline': timeline,
                            'legacy_future_entries': future_legacy,
                            'overdue_units': sum(r['units'] for r in pending if _date(r['date']) < today),
                            'warnings': (['Проверьте старые будущие записи терапии'] if future_legacy else []) +
                                        (['Есть просроченные назначения: резерв сохранен до отмены или выдачи'] if any(_date(r['date']) < today for r in pending) else [])}
    return result


def get_patient_weight(db_path, pid, on_date=None):
    day = _date(on_date) if on_date is not None else Date.today()
    with _connection(db_path) as con:
        _patient(con, pid)
        row = con.execute('SELECT weight_kg FROM medication_weights WHERE pid=? AND effective_date<=? ORDER BY effective_date DESC LIMIT 1',
                          (pid, day.isoformat())).fetchone()
        if row:
            return row[0]
        return con.execute('SELECT weight FROM patients WHERE id=?', (pid,)).fetchone()[0]


def update_pending_weight(db_path, pid, effective_date, weight_kg, today=None):
    effective = _date(effective_date)
    weight = str(_decimal(weight_kg))
    today = _date(today) if today is not None else Date.today()
    with _connection(db_path, True) as con:
        _patient(con, pid)
        con.execute('INSERT INTO medication_weights(pid,effective_date,weight_kg) VALUES(?,?,?) ON CONFLICT(pid,effective_date) DO UPDATE SET weight_kg=excluded.weight_kg',
                    (pid, effective.isoformat(), weight))
        next_weight = con.execute('SELECT MIN(effective_date) FROM medication_weights WHERE pid=? AND effective_date>?', (pid,effective.isoformat())).fetchone()[0]
        rows = con.execute("SELECT * FROM medication_events WHERE pid=? AND date>=? AND status='planned'", (pid,effective.isoformat())).fetchall()
        changed = 0
        for row in rows:
            if next_weight and row['date'] >= next_weight:
                continue
            event = recalculate_event(dict(row), weight)
            con.execute('UPDATE medication_events SET weight_kg=?,dose_mg=?,units=? WHERE id=?',
                        (event['weight_kg'], event['dose_mg'], event['units'], row['id']))
            changed += 1
        if effective <= today:
            current = con.execute('SELECT weight_kg FROM medication_weights WHERE pid=? AND effective_date<=? ORDER BY effective_date DESC LIMIT 1',
                                  (pid,today.isoformat())).fetchone()[0]
            con.execute('UPDATE patients SET weight=? WHERE id=?', (current,pid))
        return changed


def _reschedule(con, pid, event_id, new_date, reset_cadence=False):
    new_day = _date(new_date)
    event = _owned_event(con, pid, event_id)
    if event['status'] != 'planned':
        raise ValueError('Переносить можно только запланированное событие')
    moves = [(event, new_day)]
    if reset_cadence:
        future = con.execute("SELECT * FROM medication_events WHERE pid=? AND drug=? AND status='planned' AND phase='maintenance' AND extra=0 AND date>? AND id!=? ORDER BY date,id",
                             (pid,event['drug'],event['date'],event_id)).fetchall()
        interval = event['interval_weeks'] or (future[0]['interval_weeks'] if future else 0)
        if future and interval <= 0:
            raise ValueError('Не указан интервал будущей поддержки')
        occupied = {r[0] for r in con.execute("SELECT date FROM medication_events WHERE pid=? AND drug=? AND status='issued'", (pid,event['drug']))}
        day = new_day
        for row in future:
            day += timedelta(weeks=interval)
            while day.isoformat() in occupied:
                day += timedelta(weeks=interval)
            moves.append((dict(row), day))
    moved_ids = {row['id'] for row, _ in moves}
    occupied = {r['date'] for r in con.execute("SELECT id,date FROM medication_events WHERE pid=? AND drug=? AND status!='cancelled'", (pid,event['drug'])) if r['id'] not in moved_ids}
    destinations = set()
    for row, day in moves:
        if not row['extra'] and (day.isoformat() in occupied or day.isoformat() in destinations):
            raise ValueError('На новую дату уже есть событие этого препарата')
        destinations.add(day.isoformat())
        old_history = con.execute('SELECT 1 FROM medication_weights WHERE pid=? AND effective_date<=?', (pid,row['date'])).fetchone()
        new_weight = con.execute('SELECT weight_kg FROM medication_weights WHERE pid=? AND effective_date<=? ORDER BY effective_date DESC LIMIT 1',
                                 (pid,day.isoformat())).fetchone()
        weight = new_weight[0] if new_weight else row['weight_kg']
        if old_history and not new_weight:
            # No earlier history was recorded; do not invent the earlier weight.
            raise ValueError('Для переноса до первой записи веса сначала внесите вес на эту дату')
        revised = recalculate_event(row,weight) if weight is not None else row
        con.execute('UPDATE medication_events SET date=?,rescheduled=1,weight_kg=?,dose_mg=?,units=? WHERE id=?',
                    (day.isoformat(),revised['weight_kg'],revised['dose_mg'],revised['units'],row['id']))
    return _owned_event(con,pid,event_id)


def reschedule_event(db_path, pid, event_id, new_date, reset_cadence=False):
    """Move pending events only; cadence reset retains their count and snapshots of issued events."""
    with _connection(db_path, True) as con:
        return _reschedule(con,pid,event_id,new_date,reset_cadence)


def save_extra_event(db_path, pid, event, reset_cadence=False):
    """Save a manual extra dose and optional cadence reset in one transaction."""
    if not event.get('extra') or event.get('phase') != 'manual':
        raise ValueError('Требуется дополнительная запись с ручной дозой')
    with _connection(db_path, True) as con:
        event_id = _save_plan(con,pid,[event])[0]
        if reset_cadence:
            _reschedule(con,pid,event_id,event['date'],True)
        return event_id
