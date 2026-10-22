import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from homework_gui import HomeworkWindow
from prompt_config import default_templates, load_templates, save_templates
from prompt_settings_dialog import PromptSettingsDialog
from test_answers import example

ROOT = Path(__file__).resolve().parents[1]
APP = QApplication.instance() or QApplication([])
APP.setStyle('Fusion')


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.folder = Path(self.temp.name)
        self.config = self.folder/'prompts.json'

    def tearDown(self):
        self.temp.cleanup()

    def test_custom_template_cli_keeps_mapping_and_latex_protocol(self):
        rows = example(self.folder)
        templates = default_templates()
        self.assertEqual([item['name'] for item in load_templates(self.config)], ['模电', '数电'])
        templates[1]['text'] = r'请使用卡诺图验证所有结果，保留 $\overline{AB}$。{这是普通文字}'
        save_templates(self.config, templates)
        self.assertEqual(load_templates(self.config), templates)
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(ROOT/'answers_homework.py'),
                                 'prompt', str(self.folder), '--first', '2', '--last', '3',
                                 '--prompt-config', str(self.config), '--prompt-template', '数电', '--json'],
                                capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        text = json.loads(result.stdout)['prompt']
        self.assertIn(templates[1]['text'], text)
        self.assertIn('不要用 C̄D̄', text)
        self.assertIn(r'$\overline{C}\,\overline{D}$', text)
        self.assertIn(rows[1]['key'], text)
        self.assertIn(rows[2]['key'], text)
        self.assertNotIn(rows[0]['key'], text)

    def test_dialog_drafts_save_cancel_and_invalid_input_protection(self):
        save_templates(self.config, default_templates())
        dialog = PromptSettingsDialog(self.config, selected_id='digital')
        dialog.show()
        self.assertEqual(dialog.name_edit.text(), '数电')
        dialog.text_edit.setPlainText('数电编辑草稿')
        dialog.template_list.setCurrentRow(0)
        dialog.text_edit.setPlainText('模电编辑草稿')
        dialog.template_list.setCurrentRow(1)
        self.assertEqual(dialog.text_edit.toPlainText(), '数电编辑草稿')
        QTest.mouseClick(dialog.add_button, Qt.MouseButton.LeftButton)
        dialog.name_edit.setText('电路')
        dialog.text_edit.setPlainText('电路计算要求')
        QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        templates = load_templates(self.config)
        self.assertEqual([item['text'] for item in templates], ['模电编辑草稿', '数电编辑草稿', '电路计算要求'])
        before = self.config.read_bytes()
        cancelled = PromptSettingsDialog(self.config)
        cancelled.delete_template()
        cancelled.reject()
        self.assertEqual(self.config.read_bytes(), before)
        invalid = PromptSettingsDialog(self.config)
        invalid.text_edit.clear()
        invalid.save()
        self.assertNotEqual(invalid.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(self.config.read_bytes(), before)
        invalid.reject()
        self.config.write_text('{broken', encoding='utf-8')
        broken = PromptSettingsDialog(self.config)
        self.assertFalse(broken.save_button.isEnabled())
        broken.save()
        self.assertEqual(self.config.read_text(encoding='utf-8'), '{broken')
        broken.reject()
        for item in (dialog, cancelled, invalid, broken):
            item.deleteLater()
        APP.processEvents()

    def test_selected_template_is_global_and_restored_on_restart(self):
        for name in ('a.mhtml', 'b.mhtml'):
            (self.folder/name).write_text('placeholder', encoding='utf-8')
        state = self.folder/'state.json'
        api_config = self.folder/'api.json'
        window = HomeworkWindow(self.folder, state, api_config, self.config)
        try:
            window.prompt_template_combo.setCurrentIndex(window.prompt_template_combo.findData('digital'))
            window.file_list.setCurrentRow(1)
            self.assertEqual(window.prompt_template_combo.currentData(), 'digital')
            window.save_state()
            self.assertEqual(json.loads(state.read_text(encoding='utf-8'))['selected_prompt_template'], 'digital')
        finally:
            window.close()
            window.deleteLater()
            APP.processEvents()
        reopened = HomeworkWindow(self.folder, state, api_config, self.config)
        try:
            self.assertEqual(reopened.prompt_template_combo.currentData(), 'digital')
        finally:
            reopened.close()
            reopened.deleteLater()
            APP.processEvents()


if __name__ == '__main__':
    unittest.main()
