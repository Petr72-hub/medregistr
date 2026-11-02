import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'children.db'

    def connect(self):
        connection = sqlite3.connect(self.db)
        self.addCleanup(connection.close)
        return connection

    def test_historical_medapp_backup_and_preservation(self):
        c = self.connect()
        c.executescript('''
            CREATE TABLE patients(id INTEGER PRIMARY KEY,last TEXT,first TEXT,middle TEXT,
                birth TEXT,mkb TEXT,complaints TEXT,life TEXT,disease TEXT);
            CREATE TABLE analyses(id INTEGER PRIMARY KEY,pid INTEGER,name TEXT,result TEXT,file TEXT);
            INSERT INTO patients VALUES(42,'Test','Patient','', '01.01.2000','K29','keep1','keep2','keep3');
            INSERT INTO analyses VALUES(7,42,'ОАК','старое значение','old.pdf');
            CREATE TABLE unknown_extension(value TEXT);
            INSERT INTO unknown_extension VALUES('retained');
        ''')
        before = list(c.iterdump())
        backup = storage.prepare_database(self.db)
        self.assertIsNotNone(backup)
        backup_connection = sqlite3.connect(backup)
        try:
            self.assertEqual(list(backup_connection.iterdump()), before)
        finally:
            backup_connection.close()
        self.assertEqual(c.execute('SELECT complaints,life,disease FROM patients WHERE id=42').fetchone(),
                         ('keep1','keep2','keep3'))
        block = storage.load_analyses(self.db,42)[0]
        self.assertEqual(block['type'],'ОАК')
        self.assertEqual(block['data'], {'Старый результат':'старое значение'})
        self.assertEqual(c.execute('SELECT name,result,file FROM analyses WHERE id=7').fetchone(),
                         ('ОАК','старое значение','old.pdf'))
        self.assertIsNone(storage.prepare_database(self.db))
        self.assertEqual(c.execute('SELECT value FROM unknown_extension').fetchone()[0],'retained')

    def test_historical_meddapp_headers_units_and_aliases(self):
        c = self.connect()
        c.executescript('''
            CREATE TABLE patients(id INTEGER PRIMARY KEY,district TEXT,pediatrician TEXT,is_disabled INTEGER);
            INSERT INTO patients VALUES(4,'12','Test Doctor',1);
            CREATE TABLE analysis_headers(id TEXT PRIMARY KEY,pid INTEGER,type_name TEXT,file_path TEXT);
            CREATE TABLE analysis_details(id INTEGER PRIMARY KEY,header_id TEXT,param_name TEXT,result TEXT,unit TEXT);
            INSERT INTO analysis_headers VALUES('uuid-1',4,'Биохимия','file.pdf');
            INSERT INTO analysis_details VALUES(1,'uuid-1','Креатинин','42','мкмоль/л');
            INSERT INTO analysis_details VALUES(2,'uuid-1','Креатинин','43','мкмоль/л');
            CREATE TABLE therapies(id INTEGER PRIMARY KEY,pid INTEGER,drug_id INTEGER);
            INSERT INTO therapies VALUES(20,4,6);
        ''')
        storage.prepare_database(self.db)
        self.assertEqual(c.execute('SELECT uch,doctor,invalid FROM patients').fetchone(),('12','Test Doctor',1))
        block = storage.load_analyses(self.db,4)[0]
        self.assertEqual(len(block['data']['_legacy_details']),2)
        self.assertEqual(block['data']['_legacy_details'][0]['unit'],'мкмоль/л')
        self.assertEqual(c.execute('SELECT COUNT(*) FROM analysis_details').fetchone()[0],2)
        self.assertEqual(c.execute('SELECT * FROM therapies').fetchall(),[(20,4,6)])
        self.assertIsNone(storage.prepare_database(self.db))
        self.assertEqual(len(storage.load_analyses(self.db,4)),1)

    def populate(self):
        storage.prepare_database(self.db)
        c = self.connect()
        c.execute('INSERT INTO patients(id,last) VALUES(1,?), (2,?)',('First','Second'))
        c.commit()
        return c

    def test_atomic_patient_isolation_and_roundtrip(self):
        self.populate()
        other_id = storage.save_analyses(self.db,2,[{'type':'ОАМ','data':{'Цвет':'желтый'}}])[0]
        with self.assertRaises(ValueError):
            storage.save_analyses(self.db,1,[{'type':'ОАК','data':{'Гемоглобин':'120'}},
                                            {'id':other_id,'data':{'Цвет':'wrong'}}])
        self.assertEqual(storage.load_analyses(self.db,1),[])
        self.assertEqual(storage.load_analyses(self.db,2)[0]['data'],{'Цвет':'желтый'})
        own = storage.save_analyses(self.db,1,[{'type':'ОАК','data':{'Гемоглобин':'120'},'file':'files/a.pdf'}])[0]
        self.assertEqual(storage.load_analyses(self.db,1)[0]['id'],own)
        self.assertEqual(storage.load_analyses(self.db,1)[0]['file'],'files/a.pdf')

    def test_unknown_fields_omitted_rows_and_nested_data_survive(self):
        c = self.populate()
        ids = storage.save_analyses(self.db,1,[{'type':'ОАК','data':{'known':'1','unknown':{'unit':'x','value':'2'}}},
                                             {'type':'future','data':{'text':'keep'}}])
        c.execute('ALTER TABLE analyses ADD COLUMN custom_field TEXT')
        c.execute('UPDATE analyses SET custom_field=?',('keep',))
        c.commit()
        storage.save_analyses(self.db,1,[{'db_id':ids[0],'data':{'known':'3','unknown':{'value':'4'}}}])
        blocks = storage.load_analyses(self.db,1)
        self.assertEqual(len(blocks),2)
        self.assertEqual(blocks[0]['data'],{'known':'3','unknown':{'unit':'x','value':'4'}})
        self.assertEqual(blocks[0]['custom_field'],'keep')
        self.assertEqual(blocks[1]['data'],{'text':'keep'})

    def test_malformed_json_cannot_be_overwritten(self):
        c = self.populate()
        c.execute("INSERT INTO analyses(id,pid,type,data) VALUES(6,1,'ОАК','broken{')")
        c.commit()
        self.assertTrue(storage.load_analyses(self.db,1)[0]['malformed'])
        with self.assertRaises(ValueError):
            storage.save_analyses(self.db,1,[{'id':6,'data':{'new':'value'}}])
        self.assertEqual(c.execute('SELECT data FROM analyses WHERE id=6').fetchone()[0],'broken{')

    def test_invalid_database_is_not_overwritten(self):
        self.db.write_bytes(b'not a database')
        with self.assertRaises(sqlite3.DatabaseError):
            storage.prepare_database(self.db)
        self.assertEqual(self.db.read_bytes(),b'not a database')

    def test_failed_historical_import_rolls_back_schema(self):
        c = self.connect()
        c.executescript('''
            CREATE TABLE patients(id INTEGER PRIMARY KEY);
            CREATE TABLE analysis_headers(id TEXT PRIMARY KEY,pid INTEGER,type_name TEXT,file_path TEXT);
            CREATE TABLE analysis_details(id INTEGER PRIMARY KEY,header_id TEXT);
            INSERT INTO analysis_headers VALUES('broken',1,'ОАК','');
        ''')
        before = list(c.iterdump())
        with self.assertRaises(sqlite3.OperationalError):
            storage.prepare_database(self.db)
        self.assertEqual(list(c.iterdump()),before)
        self.assertEqual(len(list(self.db.parent.glob('*.backup-*'))),1)

    def test_current_repository_schema_needs_no_migration(self):
        # Read-only access to the distributed database; migrate only a temporary copy.
        source = Path(storage.__file__).parent / 'dist' / 'children.db'
        if not source.exists():
            self.skipTest('Distributed database not present')
        self.db.write_bytes(source.read_bytes())
        before = self.db.read_bytes()
        self.assertIsNone(storage.prepare_database(self.db))
        self.assertEqual(self.db.read_bytes(),before)

    def test_path_precedence(self):
        with patch.dict('os.environ',{'MEDREGISTR_DB':str(self.db)}):
            self.assertEqual(storage.resolve_paths(),(self.db.resolve(),self.db.resolve().parent/'files'))
        with patch.dict('os.environ',{},clear=True), patch.object(storage.sys,'frozen',True,create=True), \
                patch.object(storage.sys,'executable',str(self.db.parent/'App.exe')):
            self.assertEqual(storage.resolve_paths()[0],self.db.resolve())


if __name__ == '__main__':
    unittest.main()
