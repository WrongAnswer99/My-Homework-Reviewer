import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from api_config import load_profiles, new_profile, save_profiles
from api_settings_dialog import APISettingsDialog
from homework_gui import HomeworkWindow
from test_api import ROOT, answer_blocks, api_server, fixture, sse


APP = QApplication.instance() or QApplication([])
APP.setStyle('Fusion')


class APIGUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.folder = Path(self.temp.name)
        self.config = self.folder/'api_config.json'
        self.state = self.folder/'state.json'
        self.image, self.rows = fixture(self.folder)
        (self.folder/'api.mhtml').write_text('archive placeholder')
        manifest = {'source_file': 'api.mhtml', 'questions_per_image': 2,
                    'pages': [{'image': self.image.name, 'first': 1, 'last': 2,
                               'question_count': 2, 'width': 300, 'height': 400}]}
        (self.folder/'manifest.json').write_text(json.dumps(manifest))
        self.profile = new_profile()
        self.profile.update(name='电路 API', model='mock-vision', api_key='TEST-KEY-ONLY',
                            base_url='http://127.0.0.1:1/v1')
        save_profiles(self.config, [self.profile])
        self.window = HomeworkWindow(self.folder, self.state, self.config)
        self.window._load_export(self.folder)
        self.window._load_cards(self.folder)
        self.window._update_buttons()
        self.window.show()
        QTest.qWait(60)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()
        self.temp.cleanup()

    def wait_until(self, predicate, timeout=15):
        deadline = time.monotonic()+timeout
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(25)
        self.assertTrue(predicate(), self.window.status_label.text())

    def test_profile_editor_preserves_drafts_save_cancel_delete_and_global_selection(self):
        dialog = APISettingsDialog(self.config, self.window)
        dialog.show()
        dialog.name_edit.setText('模型一')
        dialog.extra_edit.setPlainText('{"enable_thinking": false}')
        QTest.mouseClick(dialog.add_button, Qt.LeftButton)
        dialog.name_edit.setText('模型二')
        dialog.model_edit.setText('mock-second')
        dialog.base_url_edit.setText('https://example.invalid/v1')
        dialog.api_key_edit.setText('SECOND-TEST-KEY')
        dialog.profile_list.setCurrentRow(0)
        self.assertEqual(dialog.name_edit.text(), '模型一')
        self.assertIn('enable_thinking', dialog.extra_edit.toPlainText())
        QTest.mouseClick(dialog.save_button, Qt.LeftButton)
        self.assertEqual(dialog.result(), QDialog.Accepted)
        profiles = load_profiles(self.config)
        self.assertEqual([p['name'] for p in profiles], ['模型一', '模型二'])
        self.assertFalse(profiles[0]['extra_body']['enable_thinking'])
        before = self.config.read_bytes()
        cancelled = APISettingsDialog(self.config, self.window)
        cancelled.delete_profile()
        self.assertEqual(cancelled.profile_list.count(), 1)
        cancelled.reject()
        self.assertEqual(self.config.read_bytes(), before)
        delete = APISettingsDialog(self.config, self.window)
        delete.profile_list.setCurrentRow(1)
        delete.delete_profile()
        delete.save()
        self.assertEqual(len(load_profiles(self.config)), 1)
        save_profiles(self.config, profiles)
        self.window._refresh_api_profiles()
        self.window.api_combo.setCurrentIndex(1)
        self.window.save_state()
        self.assertEqual(json.loads(self.state.read_text(encoding='utf-8'))['selected_api'], profiles[1]['id'])
        self.assertNotIn('SECOND-TEST-KEY', self.state.read_text(encoding='utf-8'))
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()
        self.window = HomeworkWindow(self.folder, self.state, self.config)
        self.assertEqual(self.window.api_combo.currentData(), profiles[1]['id'])
        (self.folder/'other.mhtml').write_text('another placeholder')
        self.window.refresh_files()
        self.window.file_list.setCurrentRow(1)
        self.assertEqual(self.window.api_combo.currentData(), profiles[1]['id'])
        for child in (dialog, cancelled, delete):
            child.deleteLater()

    def test_model_picker_defaults_to_custom_and_fetches_without_model_or_valid_extra_json(self):
        catalog = json.dumps({'data': [{'id': 'alpha'}, {'id': 'beta'}, {'id': 'alpha'}]})
        with api_server('{}', catalog=catalog) as (url, requests):
            dialog = APISettingsDialog(self.config, self.window)
            try:
                dialog.show()
                self.assertIsNone(dialog.model_combo.currentData())
                self.assertEqual(dialog.model_combo.currentText(), '使用自定义模型id')
                self.assertFalse(dialog.model_edit.isHidden())
                self.assertEqual(dialog.model_edit.text(), self.profile['model'])
                form = dialog.editor.layout()
                base_row = form.getWidgetPosition(dialog.base_url_edit)[0]
                custom_row = form.getWidgetPosition(dialog.model_edit)[0]
                self.assertEqual(form.itemAt(base_row+1, form.ItemRole.LabelRole).widget().text(), 'API Key')
                self.assertEqual(form.itemAt(base_row+2, form.ItemRole.LabelRole).widget().text(), '模型 ID')
                self.assertEqual(custom_row, base_row+3)
                dialog.base_url_edit.setText(url)
                dialog.model_edit.clear()
                dialog.extra_edit.setPlainText('{invalid')
                QTest.mouseClick(dialog.fetch_models_button, Qt.LeftButton)
                self.assertFalse(dialog.fetch_models_button.isEnabled())
                self.wait_until(lambda: dialog.models_process is None)
                self.assertEqual([dialog.model_combo.itemText(i) for i in range(dialog.model_combo.count())],
                                 ['alpha', 'beta', '使用自定义模型id'])
                self.assertIsNone(dialog.model_combo.currentData())
                self.assertFalse(dialog.model_edit.isHidden())
                dialog.model_edit.setText('custom-kept')
                dialog.model_combo.setCurrentIndex(1)
                self.assertTrue(dialog.model_edit.isHidden())
                self.assertTrue(dialog.custom_model_label.isHidden())
                self.assertEqual(dialog._draft()['model'], 'beta')
                dialog.model_combo.setCurrentIndex(2)
                self.assertEqual(dialog.model_edit.text(), 'custom-kept')
                self.assertFalse(dialog.model_edit.isHidden())
                dialog.model_combo.setCurrentIndex(0)
                dialog.extra_edit.setPlainText('{}')
                dialog.save()
                saved = load_profiles(self.config)[0]
                self.assertEqual(saved['model'], 'alpha')
                self.assertFalse(any(key.startswith('_') for key in saved))
                self.assertFalse(requests)
            finally:
                dialog.reject()
                dialog.deleteLater()

    def test_model_picker_preserves_existing_id_drafts_and_clears_catalog_on_credentials_change(self):
        catalog = json.dumps({'data': [{'id': self.profile['model']}, {'id': 'other'}]})
        with api_server('{}', catalog=catalog) as (url, requests):
            dialog = APISettingsDialog(self.config, self.window)
            try:
                dialog.show()
                dialog.base_url_edit.setText(url)
                QTest.mouseClick(dialog.fetch_models_button, Qt.LeftButton)
                self.wait_until(lambda: dialog.models_process is None)
                self.assertEqual(dialog.model_combo.currentData(), self.profile['model'])
                dialog.model_combo.setCurrentIndex(1)
                dialog.add_profile()
                self.assertEqual(dialog.model_combo.count(), 1)
                self.assertIsNone(dialog.model_combo.currentData())
                dialog.profile_list.setCurrentRow(0)
                self.assertEqual(dialog.model_combo.currentData(), 'other')
                self.assertTrue(dialog.model_edit.isHidden())
                dialog.api_key_edit.setText('CHANGED-TEST-KEY')
                self.assertEqual(dialog.model_combo.count(), 1)
                self.assertIsNone(dialog.model_combo.currentData())
                self.assertEqual(dialog.model_edit.text(), 'other')
                self.assertFalse(dialog.model_edit.isHidden())
                QTest.mouseClick(dialog.fetch_models_button, Qt.LeftButton)
                self.assertIsNotNone(dialog.models_process)
                dialog.base_url_edit.setText('http://127.0.0.1:1/v1')
                self.assertIsNone(dialog.models_process)
                QTest.qWait(150)
                self.assertEqual(dialog.model_combo.count(), 1)
                self.assertFalse(requests)
            finally:
                dialog.reject()
                dialog.deleteLater()

    def test_model_picker_failed_or_empty_fetch_keeps_custom_input_and_cancel_does_not_save(self):
        before = self.config.read_bytes()
        for catalog in ((401, '{"error":{"message":"TEST-KEY-ONLY"}}'), '{"data": []}'):
            with api_server('{}', catalog=catalog) as (url, requests):
                dialog = APISettingsDialog(self.config, self.window)
                try:
                    dialog.show()
                    dialog.base_url_edit.setText(url)
                    dialog.model_edit.setText('private-custom-model')
                    QTest.mouseClick(dialog.fetch_models_button, Qt.LeftButton)
                    self.wait_until(lambda: dialog.models_process is None)
                    self.assertTrue(dialog.fetch_models_button.isEnabled())
                    self.assertEqual(dialog.model_combo.count(), 1)
                    self.assertEqual(dialog.model_edit.text(), 'private-custom-model')
                    self.assertNotIn(self.profile['api_key'], dialog.message.text())
                    self.assertFalse(requests)
                finally:
                    dialog.reject()
                    dialog.deleteLater()
                self.assertEqual(self.config.read_bytes(), before)

    def test_bad_json_or_corrupt_configuration_is_not_overwritten(self):
        before = self.config.read_bytes()
        dialog = APISettingsDialog(self.config, self.window)
        dialog.extra_edit.setPlainText('{invalid')
        dialog.save()
        self.assertNotEqual(dialog.result(), QDialog.Accepted)
        self.assertEqual(self.config.read_bytes(), before)
        dialog.reject()
        self.config.write_text('{broken')
        broken = APISettingsDialog(self.config, self.window)
        self.assertFalse(broken.save_button.isEnabled())
        broken.save()
        self.assertEqual(self.config.read_text(), '{broken')
        broken.reject()
        dialog.deleteLater()
        broken.deleteLater()

    def test_streamed_card_answers_pass_through_cli_and_merge_automatically(self):
        self.window.prompt_template_combo.setCurrentIndex(self.window.prompt_template_combo.findData('digital'))
        self.window.prompt.setPlainText('原输入，等待完整解答后替换')
        self.window.save_state()
        with api_server(sse(answer_blocks(self.rows)), 'text/event-stream') as (url, requests):
            save_profiles(self.config, [dict(self.profile, base_url=url)])
            chunks = []
            handle = self.window._api_event
            def record(event):
                chunks.append(event)
                handle(event)
                if event.get('type') == 'delta':
                    self.assertFalse((self.folder/'answers.json').exists())
                    self.assertFalse(self.window.answer_timer.isActive())
            self.window._api_event = record
            QTest.mouseClick(self.window.call_api_button, Qt.LeftButton)
            self.assertEqual(self.window.job['kind'], 'api')
            self.assertTrue(self.window.prompt.isReadOnly())
            self.assertFalse(self.window.api_config_button.isEnabled())
            self.assertIn(str(ROOT/'api_homework.py'), self.window.process.arguments())
            self.assertNotIn(self.profile['api_key'], ' '.join(self.window.process.arguments()))
            self.wait_until(lambda: self.window.job is None)
            self.assertEqual(len(requests), 1)
            sent = requests[0]['body']['messages'][-1]['content'][0]['text']
            self.assertIn('格雷码', sent)
            self.assertIn(r'$\overline{C}\,\overline{D}$', sent)
            self.assertTrue(any(chunk['type'] == 'delta' for chunk in chunks))
            self.assertEqual(self.window.answered, {1, 2})
            self.assertEqual(self.window.prompt.toPlainText(), answer_blocks(self.rows))
            self.assertTrue(self.window.open_study_button.isEnabled())
            self.assertFalse(self.window.prompt.isReadOnly())
            self.assertTrue(self.window.api_config_button.isEnabled())
            self.assertIn('$I=1\\,mA$', self.window.study_path.read_text(encoding='utf-8'))
            self.assertEqual((self.folder/'questions.md').read_text(encoding='utf-8'), '原题不覆盖')

    def test_answer_drafts_follow_selected_card_and_survive_restart(self):
        second = self.folder/'card2.png'
        second.write_bytes(self.image.read_bytes())
        manifest = json.loads((self.folder/'manifest.json').read_text())
        manifest['pages'] = [dict(manifest['pages'][0], first=1, last=1, question_count=1),
                             dict(manifest['pages'][0], image=second.name, first=2, last=2,
                                  question_count=1)]
        (self.folder/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        self.window._load_cards(self.folder)
        self.window.prompt.setPlainText('第 1 题草稿')
        self.window.cards.setCurrentRow(1)
        self.assertEqual(self.window.prompt.toPlainText(), '')
        self.window.prompt.setPlainText('第 2 题草稿')
        self.window.cards.setCurrentRow(0)
        self.assertEqual(self.window.prompt.toPlainText(), '第 1 题草稿')
        self.window.cards.setCurrentRow(1)
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()
        saved = json.loads(self.state.read_text(encoding='utf-8'))['files'][str(self.folder/'api.mhtml')]
        self.assertEqual(saved['card_texts']['1-1'], '第 1 题草稿')
        self.assertEqual(saved['card_texts']['2-2'], '第 2 题草稿')
        self.window = HomeworkWindow(self.folder, self.state, self.config)
        self.assertEqual(self.window.cards.currentRow(), 1)
        self.assertEqual(self.window.prompt.toPlainText(), '第 2 题草稿')
        self.window.cards.setCurrentRow(0)
        self.assertEqual(self.window.prompt.toPlainText(), '第 1 题草稿')

    def test_switching_cards_immediately_after_paste_merges_previous_answer(self):
        second = self.folder/'card2.png'
        second.write_bytes(self.image.read_bytes())
        manifest = json.loads((self.folder/'manifest.json').read_text())
        manifest['pages'] = [dict(manifest['pages'][0], first=1, last=1, question_count=1),
                             dict(manifest['pages'][0], image=second.name, first=2, last=2,
                                  question_count=1)]
        (self.folder/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        self.window._load_cards(self.folder)
        first_answer = answer_blocks(self.rows[:1])
        self.window.prompt.setPlainText(first_answer)
        self.window.cards.setCurrentRow(1)
        self.assertEqual(self.window.prompt.toPlainText(), '')
        self.wait_until(lambda: self.window.job is None and self.window.answered == {1})
        self.window.cards.setCurrentRow(0)
        self.assertEqual(self.window.prompt.toPlainText(), first_answer)

    def test_api_reply_stays_on_its_card_while_another_card_is_edited(self):
        second = self.folder/'card2.png'
        second.write_bytes(self.image.read_bytes())
        manifest = json.loads((self.folder/'manifest.json').read_text())
        manifest['pages'] = [dict(manifest['pages'][0], first=1, last=1, question_count=1),
                             dict(manifest['pages'][0], image=second.name, first=2, last=2,
                                  question_count=1)]
        (self.folder/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        self.window._load_cards(self.folder)
        first_answer = answer_blocks(self.rows[:1])
        second_answer = (answer_blocks(self.rows[:1]).replace('$I=1\\,mA$', '手动覆盖第一题')
                         +'\n\n'+answer_blocks(self.rows[1:]).replace('$I=1\\,mA$', '第二题结论：错误'))
        release = threading.Event()

        def delayed_reply(_request):
            release.wait(timeout=10)
            return sse(first_answer)

        with api_server(delayed_reply, 'text/event-stream') as (url, requests):
            try:
                save_profiles(self.config, [dict(self.profile, base_url=url)])
                self.window.start_api()
                self.wait_until(lambda: bool(requests))
                self.assertTrue(self.window.cards.isEnabled())
                self.assertTrue(self.window.prompt.isReadOnly())
                self.window.cards.setCurrentRow(1)
                self.assertFalse(self.window.prompt.isReadOnly())
                self.window.prompt.setPlainText(second_answer)
                self.window._api_event({'type': 'delta', 'text': '第一题的部分回复'})
                self.assertEqual(self.window.prompt.toPlainText(), second_answer)
                self.window.cards.setCurrentRow(0)
                self.assertEqual(self.window.prompt.toPlainText(), '第一题的部分回复')
                self.assertTrue(self.window.prompt.isReadOnly())
                self.window.cards.setCurrentRow(1)
                self.assertEqual(self.window.prompt.toPlainText(), second_answer)
                self.assertIn('2-2', self.window.pending_merges)
                release.set()
                self.wait_until(lambda: self.window.job is None and self.window.answered == {1, 2})
                self.assertEqual(self.window.prompt.toPlainText(), second_answer)
                self.window.cards.setCurrentRow(0)
                self.assertEqual(self.window.prompt.toPlainText(), first_answer)
                document = self.window.study_path.read_text(encoding='utf-8')
                first_section, second_section = document.split('### 第 2 题', 1)
                self.assertIn('$I=1\\,mA$', first_section)
                self.assertNotIn('手动覆盖第一题', first_section)
                self.assertIn('第二题结论：错误', second_section)
            finally:
                release.set()

    def test_review_correction_drafts_and_api_result_follow_question(self):
        panel = self.window.correction_panel
        self.window.tabs.setCurrentWidget(self.window.correction_panel)
        self.wait_until(lambda: panel.detail is not None and panel.detail['order'] == 1)
        panel.correction_note.setText('平台答案 A：方向反了')
        panel.correction_answer.setPlainText('第一题手工草稿')
        panel.question_list.setCurrentRow(1)
        self.assertEqual(panel.correction_note.text(), '')
        panel.correction_note.setText('第二题平台说明')
        panel.correction_answer.setPlainText('第二题手工草稿')
        panel.question_list.setCurrentRow(0)
        self.assertEqual(panel.correction_note.text(), '平台答案 A：方向反了')
        self.wait_until(lambda: panel.job is None and not panel.queue and
                        (self.folder/'review.json').is_file())
        saved = json.loads((self.folder/'review.json').read_text(encoding='utf-8'))['questions']
        self.assertEqual(saved[self.rows[0]['key']]['correction_note'], '平台答案 A：方向反了')
        self.assertEqual(saved[self.rows[1]['key']]['corrected_answer'], '第二题手工草稿')
        image = self.folder/'review_images'/'question_0001.png'
        image.parent.mkdir()
        image.write_bytes(self.image.read_bytes())
        QTest.mouseClick(panel.copy_image_button, Qt.LeftButton)
        self.wait_until(lambda: panel.job is None and not panel.queue and QApplication.clipboard().mimeData().hasImage())
        QTest.mouseClick(panel.copy_correction_prompt_button, Qt.LeftButton)
        self.wait_until(lambda: panel.job is None and not panel.queue and
                        '平台答案 A：方向反了' in QApplication.clipboard().text())
        release = threading.Event()
        retry_release = threading.Event()
        corrected = answer_blocks(self.rows[:1]).replace('$I=1\\,mA$', '修正结论 A')

        def delayed_reply(_request):
            (retry_release if len(requests) > 1 else release).wait(timeout=10)
            return sse(corrected)

        with api_server(delayed_reply, 'text/event-stream') as (url, requests):
            try:
                save_profiles(self.config, [dict(self.profile, base_url=url, send_image=False)])
                self.window._refresh_api_profiles()
                panel.start_correction_api()
                self.wait_until(lambda: bool(requests))
                self.assertTrue(panel.correction_answer.isReadOnly())
                panel.question_list.setCurrentRow(1)
                self.assertFalse(panel.correction_answer.isReadOnly())
                panel.correction_answer.setPlainText('第二题继续修改')
                panel._api_event({'type': 'delta', 'text': '第一题流式内容'})
                self.assertEqual(panel.correction_answer.toPlainText(), '第二题继续修改')
                self.window.tabs.setCurrentWidget(self.window.review_panel)
                self.wait_until(lambda: self.window.review_panel.detail is not None)
                self.assertFalse(self.window.review_panel.correction_note.isVisible())
                self.window.tabs.setCurrentWidget(self.window.correction_panel)
                self.wait_until(lambda: panel.detail is not None and panel.detail['order'] == 2)
                self.assertEqual(panel.correction_answer.toPlainText(), '第二题继续修改')
                release.set()
                self.wait_until(lambda: panel.api_job is None and panel.job is None and not panel.queue)
                self.assertEqual(panel.correction_answer.toPlainText(), '第二题继续修改')
                panel.question_list.setCurrentRow(0)
                self.assertIn('修正结论 A', panel.correction_answer.toPlainText())
                saved = json.loads((self.folder/'review.json').read_text(encoding='utf-8'))['questions']
                self.assertIn('修正结论 A', saved[self.rows[0]['key']]['corrected_answer'])
                self.assertEqual(saved[self.rows[1]['key']]['corrected_answer'], '第二题继续修改')
                content = requests[0]['body']['messages'][-1]['content']
                self.assertIn('平台答案 A', content)
                QTest.mouseClick(panel.retry_correction_api_button, Qt.LeftButton)
                self.wait_until(lambda: len(requests) == 2)
                QTest.mouseClick(panel.call_correction_api_button, Qt.LeftButton)
                self.wait_until(lambda: panel.api_job is None)
                self.assertIn('修正结论 A', panel.correction_answer.toPlainText())
                panel.correction_note.setText('关闭前立即修改的纠错信息')
                self.window.close()
                saved = json.loads((self.folder/'review.json').read_text(encoding='utf-8'))['questions']
                self.assertEqual(saved[self.rows[0]['key']]['correction_note'], '关闭前立即修改的纠错信息')
            finally:
                release.set()
                retry_release.set()

    def test_model_catalog_filters_efforts_and_uses_declared_request_field(self):
        catalog = json.dumps({'data': [{
            'id': self.profile['model'], 'reasoning': {'supported_efforts': ['low', 'high']},
            'supported_parameters': ['reasoning']}]})
        with api_server(sse(answer_blocks(self.rows)), 'text/event-stream', catalog=catalog) as (url, requests):
            save_profiles(self.config, [dict(self.profile, base_url=url)])
            self.window._request_reasoning_info()
            self.wait_until(lambda: self.window.reasoning_hint.text() == '2 档可选')
            self.assertEqual([self.window.reasoning_combo.itemData(i)
                              for i in range(self.window.reasoning_combo.count())], [None, 'low', 'high'])
            self.window.reasoning_combo.setCurrentIndex(2)
            self.window.start_api()
            self.assertIn('--reasoning-parameter', self.window.process.arguments())
            self.wait_until(lambda: self.window.job is None)
            self.assertEqual(self.window.answered, {1, 2})
            self.assertEqual(requests[0]['body']['reasoning'], {'effort': 'high'})
            self.assertNotIn('reasoning_effort', requests[0]['body'])

    def test_retry_reasoning_queries_catalog_again_and_keeps_selected_level(self):
        lookups = []
        def catalog(path):
            lookups.append(path)
            model = {'id': self.profile['model']}
            if len(lookups) > 1:
                model.update(reasoning={'supported_efforts': ['low', 'high']},
                             supported_parameters=['reasoning'])
            return json.dumps({'data': [model]})
        with api_server('{}', catalog=catalog) as (url, _):
            save_profiles(self.config, [dict(self.profile, base_url=url)])
            self.window._request_reasoning_info()
            self.wait_until(lambda: len(lookups) == 1 and self.window.reasoning_process is None)
            self.assertEqual(self.window.reasoning_hint.text(), '档位未提供')
            QTest.mouseClick(self.window.retry_reasoning_button, Qt.LeftButton)
            self.wait_until(lambda: len(lookups) == 2 and self.window.reasoning_hint.text() == '2 档可选')
            self.window.reasoning_combo.setCurrentIndex(self.window.reasoning_combo.findData('high'))
            QTest.mouseClick(self.window.retry_reasoning_button, Qt.LeftButton)
            self.wait_until(lambda: len(lookups) == 3 and self.window.reasoning_process is None)
            self.assertEqual(self.window.reasoning_combo.currentData(), 'high')
            self.assertEqual(self.window.reasoning_parameter, 'reasoning')

    def test_manual_retry_probes_missing_metadata_and_syncs_model_specific_levels(self):
        model = self.profile['model']
        error = json.dumps({'error': {'message': f'{model} does not support reasoning effort "none"; '
                                                'use low, medium, high, xhigh or max'}})
        catalog = json.dumps({'data': [{'id': model}]})
        with api_server(error, status=400, catalog=catalog) as (url, requests):
            save_profiles(self.config, [dict(self.profile, base_url=url)])
            self.window._request_reasoning_info()
            self.wait_until(lambda: self.window.reasoning_process is None)
            self.assertEqual(self.window.reasoning_hint.text(), '档位未提供')
            self.assertFalse(requests)
            self.window.reasoning_combo.setCurrentIndex(self.window.reasoning_combo.findData('high'))
            QTest.mouseClick(self.window.retry_reasoning_button, Qt.LeftButton)
            self.wait_until(lambda: self.window.reasoning_hint.text() == '5 档可选')
            expected = [None, 'low', 'medium', 'high', 'xhigh', 'max']
            self.assertEqual([self.window.reasoning_combo.itemData(i)
                              for i in range(self.window.reasoning_combo.count())], expected)
            self.assertEqual(self.window.reasoning_combo.currentData(), 'high')
            combo = self.window.correction_panel.review_reasoning_combo
            self.assertEqual([combo.itemData(i) for i in range(combo.count())], expected)
            self.assertEqual(len(requests), 1)
            QTest.mouseClick(self.window.correction_panel.retry_review_reasoning_button, Qt.LeftButton)
            self.wait_until(lambda: len(requests) == 2 and self.window.reasoning_process is None)
            self.assertEqual(self.window.reasoning_hint.text(), '5 档可选')

    def test_provider_browser_signature_block_is_reported_without_exposing_key(self):
        blocked = json.dumps({'cloudflare_error': True, 'error_code': 1010,
                              'ray_id': 'a46e3849ab0ece01', 'detail': 'browser signature banned'})
        with api_server(blocked, status=403, catalog=(403, blocked)) as (url, requests):
            save_profiles(self.config, [dict(self.profile, base_url=url)])
            self.window._request_reasoning_info()
            self.wait_until(lambda: self.window.reasoning_hint.text() == '服务商拦截查询')
            self.assertIn('Cloudflare', self.window.status_label.text())
            self.window.start_api()
            self.wait_until(lambda: len(requests) == 1 and self.window.job is None)
            self.assertIn('Cloudflare', self.window.status_label.text())
            self.assertIn('Ray ID', self.window.status_label.text())
            self.assertNotIn(self.profile['api_key'], self.window.log.decode('utf-8'))

    def test_retry_api_resends_last_card_with_original_effort(self):
        self.window.prompt_template_combo.setCurrentIndex(self.window.prompt_template_combo.findData('digital'))
        catalog = json.dumps({'data': [{
            'id': self.profile['model'], 'reasoning': {'supported_efforts': ['low', 'high']},
            'supported_parameters': ['reasoning']}]})
        with api_server(sse(answer_blocks(self.rows)), 'text/event-stream', catalog=catalog) as (url, requests):
            save_profiles(self.config, [dict(self.profile, base_url=url)])
            self.window._request_reasoning_info()
            self.wait_until(lambda: self.window.reasoning_hint.text() == '2 档可选')
            self.assertFalse(self.window.retry_api_button.isEnabled())
            self.window.reasoning_combo.setCurrentIndex(self.window.reasoning_combo.findData('high'))
            self.window.start_api()
            prompt_snapshot = self.window.job['input']
            self.assertFalse(self.window.retry_api_button.isEnabled())
            self.wait_until(lambda: self.window.job is None and self.window.answered == {1, 2})
            self.assertTrue(self.window.retry_api_button.isEnabled())
            self.assertFalse(prompt_snapshot.exists())
            self.window.prompt_template_combo.setCurrentIndex(self.window.prompt_template_combo.findData('analog'))
            self.window.reasoning_combo.setCurrentIndex(self.window.reasoning_combo.findData('low'))
            QTest.mouseClick(self.window.retry_api_button, Qt.LeftButton)
            self.wait_until(lambda: len(requests) == 2 and self.window.job is None)
            self.assertEqual(requests[0]['body'], requests[1]['body'])
            self.assertEqual(requests[1]['body']['reasoning'], {'effort': 'high'})
            self.assertEqual(self.window.answered, {1, 2})

    def test_retry_from_another_card_returns_to_original_draft(self):
        second = self.folder/'card2.png'
        second.write_bytes(self.image.read_bytes())
        manifest = json.loads((self.folder/'manifest.json').read_text())
        manifest['pages'] = [dict(manifest['pages'][0], first=1, last=1, question_count=1),
                             dict(manifest['pages'][0], image=second.name, first=2, last=2,
                                  question_count=1)]
        (self.folder/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        self.window._load_cards(self.folder)
        with api_server(sse(answer_blocks(self.rows[:1])), 'text/event-stream') as (url, requests):
            save_profiles(self.config, [dict(self.profile, base_url=url)])
            self.window.start_api()
            self.wait_until(lambda: self.window.job is None and self.window.answered == {1})
            self.window.cards.setCurrentRow(1)
            self.window.prompt.setPlainText('第 2 题手写草稿')
            QTest.mouseClick(self.window.retry_api_button, Qt.LeftButton)
            self.assertEqual(self.window.cards.currentRow(), 0)
            self.wait_until(lambda: len(requests) == 2 and self.window.job is None)
            self.assertEqual(requests[0]['body'], requests[1]['body'])
            self.window.cards.setCurrentRow(1)
            self.assertEqual(self.window.prompt.toPlainText(), '第 2 题手写草稿')

    def test_retry_api_after_incomplete_reply_imports_only_complete_retry(self):
        def reply(request):
            rows = self.rows[:1] if len(requests) == 1 else self.rows
            return sse(answer_blocks(rows))
        with api_server(reply, 'text/event-stream') as (url, requests):
            save_profiles(self.config, [dict(self.profile, base_url=url)])
            self.window.start_api()
            self.wait_until(lambda: len(requests) == 1 and self.window.job is None)
            self.assertFalse((self.folder/'answers.json').exists())
            self.assertTrue(self.window.retry_api_button.isEnabled())
            QTest.mouseClick(self.window.retry_api_button, Qt.LeftButton)
            self.wait_until(lambda: len(requests) == 2 and self.window.job is None
                            and self.window.answered == {1, 2})
            self.assertEqual(requests[0]['body'], requests[1]['body'])
            self.assertTrue((self.folder/'questions_with_answers.md').is_file())

    def test_failure_restores_input_and_invalid_reply_remains_without_import(self):
        self.window.prompt.setPlainText('保留我的笔记')
        self.window.save_state()
        with api_server('{"error":{"message":"bad key TEST-KEY-ONLY"}}', status=401) as (url, _):
            save_profiles(self.config, [dict(self.profile, base_url=url)])
            self.window.start_api()
            self.wait_until(lambda: self.window.job is None)
            self.assertEqual(self.window.prompt.toPlainText(), '保留我的笔记')
            self.assertIn('HTTP 401', self.window.status_label.text())
            self.assertNotIn(self.profile['api_key'], self.window.log.decode('utf-8'))
        with api_server(sse(answer_blocks(self.rows[:1])), 'text/event-stream') as (url, _):
            save_profiles(self.config, [dict(self.profile, base_url=url)])
            self.window.start_api()
            self.wait_until(lambda: self.window.job is None)
            QTest.qWait(900)
            self.assertEqual(self.window.prompt.toPlainText(), answer_blocks(self.rows[:1]))
            self.assertFalse((self.folder/'answers.json').exists())
            self.assertFalse(self.window.answer_dirty)
            self.assertFalse(self.window.answer_timer.isActive())
            self.assertIn('未导入', self.window.status_label.text())

    def test_cancel_pending_request_restores_input_and_buttons(self):
        release = threading.Event()
        self.window.prompt.setPlainText('取消后保留这段输入')
        def delayed_reply(request):
            release.wait(timeout=10)
            return sse(answer_blocks(self.rows))
        with api_server(delayed_reply, 'text/event-stream') as (url, requests):
            try:
                save_profiles(self.config, [dict(self.profile, base_url=url)])
                self.window.start_api()
                self.wait_until(lambda: bool(requests))
                self.assertEqual(self.window.call_api_button.text(), '停止请求')
                QTest.mouseClick(self.window.call_api_button, Qt.LeftButton)
                self.wait_until(lambda: self.window.job is None)
                self.assertEqual(self.window.prompt.toPlainText(), '取消后保留这段输入')
                self.assertFalse((self.folder/'answers.json').exists())
                self.assertTrue(self.window.call_api_button.isEnabled())
                self.assertIn('已停止', self.window.status_label.text())
            finally:
                release.set()


if __name__ == '__main__':
    unittest.main()
