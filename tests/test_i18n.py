import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from i18n import set_language, tr


class LanguageTests(unittest.TestCase):
    def tearDown(self):
        set_language('en')

    def test_default_and_unsupported_language_use_english(self):
        set_language('unsupported')
        self.assertEqual(tr('Belum terhubung'), 'Not connected')

    def test_indonesian_settings_and_manual_delay(self):
        set_language('id')
        self.assertEqual(tr('Save'), 'Simpan')
        self.assertEqual(tr('Manual delay: 10 seconds; batch rest: 300 seconds.'),
                         'Jeda manual: 10 detik; istirahat batch: 300 detik.')

    def test_template_storage_never_translates_user_text(self):
        tree = ast.parse(Path('app.py').read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'App')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'store_editor')
        namespace = {'tr': tr}
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'app.py', 'exec'), namespace)
        for language in ('en', 'id'):
            set_language(language)
            item = {}
            fake = SimpleNamespace(body=Mock(), name=Mock(), image_path=Mock(), editor_index=0,
                                   templates=lambda: [item], persist=Mock())
            fake.body.get.return_value = 'Belum terhubung'
            fake.name.get.return_value = 'Daftar Opt-out'
            fake.image_path.get.return_value = ''
            namespace['store_editor'](fake)
            self.assertEqual(item['text'], 'Belum terhubung')
            self.assertEqual(item['name'], 'Daftar Opt-out')
