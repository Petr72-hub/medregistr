"""SQLite compatibility and atomic laboratory persistence.

Existing tables, IDs, legacy columns and unknown JSON keys are retained.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys
from datetime import datetime
from uuid import uuid4
from contextlib import closing


PATIENT_COLUMNS = {
    'last': 'TEXT', 'first': 'TEXT', 'middle': 'TEXT', 'birth': 'TEXT',
    'mkb': 'TEXT', 'gender': 'TEXT', 'address_reg': 'TEXT',
    'address_fact': 'TEXT', 'clinic': 'TEXT', 'uch': 'TEXT',
    'doctor': 'TEXT', 'phone': 'TEXT', 'invalid': 'INTEGER', 'weight': 'REAL',
}
ANALYSIS_COLUMNS = {
    'pid': 'INTEGER', 'type': 'TEXT', 'data': 'TEXT',
    'file': 'TEXT', 'created_at': 'TEXT',
}


def resolve_paths() -> tuple[Path, Path]:
    """Resolve storage independently of the process working directory."""
    configured = os.environ.get('MEDREGISTR_DB')
    if configured:
        database = Path(configured).expanduser().resolve()
    elif getattr(sys, 'frozen', False):
        database = Path(sys.executable).resolve().parent / 'children.db'
    else:
        root = Path(__file__).resolve().parent
        database = root / 'children.db'
        if not database.exists() and (root / 'dist' / 'children.db').exists():
            database = root / 'dist' / 'children.db'
    return database, database.parent / 'files'


def _columns(connection, table):
    return {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}


def prepare_database(db_path) -> Path | None:
    """Add missing fields transactionally, backing up before any migration.

    Returns the backup path or None. Therapy/stock tables remain the responsibility
    of the existing application's initializer and are never modified here.
    """
    path = Path(db_path)
    existed = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    backup_path = None
    try:
        if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise sqlite3.DatabaseError('Проверка целостности базы не пройдена')
        old_columns = {table: _columns(connection, table)
                       for table in ('patients', 'analyses')}
        for table, columns in old_columns.items():
            if columns and 'id' not in columns:
                raise sqlite3.DatabaseError(f'Нет первичного ID в таблице {table}')
        missing = any(not set(required).issubset(old_columns[table])
                      for table, required in (('patients', PATIENT_COLUMNS),
                                              ('analyses', ANALYSIS_COLUMNS)))
        legacy = False
        ac = old_columns['analyses']
        if 'name' in ac:
            legacy = 'type' not in ac or bool(connection.execute(
                "SELECT 1 FROM analyses WHERE (type IS NULL OR type='') AND name IS NOT NULL LIMIT 1"
            ).fetchone())
        if 'result' in ac:
            legacy = legacy or 'data' not in ac or bool(connection.execute(
                "SELECT 1 FROM analyses WHERE (data IS NULL OR data='') AND result IS NOT NULL LIMIT 1"
            ).fetchone())
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        historical = 'analysis_headers' in tables and 'analysis_details' in tables
        import_headers = historical and ('medregistr_imports' not in tables or bool(connection.execute(
            "SELECT 1 FROM analysis_headers h WHERE NOT EXISTS (SELECT 1 FROM medregistr_imports m WHERE m.header_id=h.id) LIMIT 1"
        ).fetchone()))
        if not missing and not legacy and not import_headers:
            return None
        if existed:
            backup_path = path.with_name(path.name + '.backup-' +
                                        datetime.now().strftime('%Y%m%d-%H%M%S') +
                                        '-' + uuid4().hex[:8])
            with closing(sqlite3.connect(backup_path)) as backup:
                connection.backup(backup)
        connection.execute('BEGIN IMMEDIATE')
        for table, required in (('patients', PATIENT_COLUMNS),
                                ('analyses', ANALYSIS_COLUMNS)):
            if not old_columns[table]:
                connection.execute(f'CREATE TABLE {table}(id INTEGER PRIMARY KEY)')
            for name, kind in required.items():
                if name not in old_columns[table]:
                    connection.execute(f'ALTER TABLE {table} ADD COLUMN {name} {kind}')
        if 'name' in ac:
            connection.execute("UPDATE analyses SET type=name WHERE (type IS NULL OR type='') AND name IS NOT NULL")
        if 'result' in ac:
            for record_id, result in connection.execute(
                "SELECT id,result FROM analyses WHERE (data IS NULL OR data='') AND result IS NOT NULL"
            ).fetchall():
                connection.execute('UPDATE analyses SET data=? WHERE id=?',
                                   (json.dumps({'Старый результат': result}, ensure_ascii=False), record_id))
        for old_name, new_name in (('district', 'uch'), ('pediatrician', 'doctor'), ('is_disabled', 'invalid')):
            if old_name in old_columns['patients'] and new_name not in old_columns['patients']:
                connection.execute(f'UPDATE patients SET {new_name}={old_name}')
        if import_headers:
            connection.execute('CREATE TABLE IF NOT EXISTS medregistr_imports(header_id TEXT PRIMARY KEY, analysis_id INTEGER NOT NULL)')
            headers = connection.execute(
                'SELECT id,pid,type_name,file_path FROM analysis_headers WHERE id NOT IN (SELECT header_id FROM medregistr_imports)'
            ).fetchall()
            for header_id, patient_id, type_name, file_path in headers:
                details = connection.execute(
                    'SELECT param_name,result,unit FROM analysis_details WHERE header_id=? ORDER BY id', (header_id,)
                ).fetchall()
                # Keep the exact original rows too: duplicate parameter names and units
                # must survive even when the UI uses a dictionary of results.
                values = {name: result for name, result, unit in details}
                values['_legacy_details'] = [{'parameter': n, 'result': r, 'unit': u} for n, r, u in details]
                cursor = connection.execute(
                    'INSERT INTO analyses(pid,type,data,file,created_at) VALUES(?,?,?,?,?)',
                    (patient_id, type_name, json.dumps(values, ensure_ascii=False), file_path, None))
                connection.execute('INSERT INTO medregistr_imports(header_id,analysis_id) VALUES(?,?)',
                                   (header_id, cursor.lastrowid))
        connection.commit()
        return backup_path
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _decode(raw):
    if raw is None or raw == '':
        return {}, False
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return {'Старый результат': raw}, True
    if not isinstance(value, dict):
        return {'Старый результат': raw}, True
    return value, False


def load_analyses(db_path, pid) -> list[dict]:
    connection = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        blocks = []
        for row in connection.execute('SELECT * FROM analyses WHERE pid=? ORDER BY id', (pid,)):
            block = dict(row)
            block['db_id'] = block['id']
            block['data'], block['malformed'] = _decode(block.get('data'))
            blocks.append(block)
        return blocks
    finally:
        connection.close()


def _merge(original, incoming):
    merged = dict(original)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def save_analyses(db_path, pid, blocks) -> list[int]:
    """Atomically upsert supplied blocks, never deleting omitted records.

    Existing IDs must belong to pid; malformed stored JSON is not overwritten.
    Unknown columns and unknown JSON keys survive updates. File copying belongs
    to the caller and must complete before this transaction is submitted.
    """
    connection = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=rw', uri=True)
    saved_ids = []
    seen_ids = set()
    try:
        connection.execute('BEGIN IMMEDIATE')
        if not connection.execute('SELECT 1 FROM patients WHERE id=?', (pid,)).fetchone():
            raise ValueError('Пациент не найден')
        for block in blocks:
            record_id = block.get('id') if block.get('id') is not None else block.get('db_id')
            data = block.get('data', {})
            if not isinstance(data, dict):
                raise ValueError('Параметры анализа должны быть объектом')
            if record_id is not None:
                if record_id in seen_ids:
                    raise ValueError('Один анализ передан дважды')
                seen_ids.add(record_id)
                old = connection.execute(
                    'SELECT type,data,file,created_at FROM analyses WHERE id=? AND pid=?',
                    (record_id, pid)).fetchone()
                if old is None:
                    raise ValueError('Анализ не найден у выбранного пациента')
                old_data, malformed = _decode(old[1])
                if malformed:
                    raise ValueError('Старый формат анализа требует отдельного преобразования')
                payload = json.dumps(_merge(old_data, data), ensure_ascii=False, allow_nan=False)
                connection.execute(
                    'UPDATE analyses SET type=?,data=?,file=?,created_at=? WHERE id=? AND pid=?',
                    (block.get('type', old[0]), payload, block.get('file', old[2]),
                     block.get('created_at', old[3]), record_id, pid))
            else:
                if not block.get('type'):
                    raise ValueError('Не выбран тип анализа')
                cursor = connection.execute(
                    'INSERT INTO analyses(pid,type,data,file,created_at) VALUES(?,?,?,?,?)',
                    (pid, block['type'], json.dumps(data, ensure_ascii=False, allow_nan=False),
                     block.get('file', ''), block.get('created_at') or datetime.now().isoformat(timespec='seconds')))
                record_id = cursor.lastrowid
            saved_ids.append(record_id)
        connection.commit()
        return saved_ids
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
