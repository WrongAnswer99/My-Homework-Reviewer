import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from answers_homework import load_questions, merge_answers
from api_config import new_profile, save_profiles
from api_homework import build_payload
from batch_homework import plan_cards
from homework_gui import HomeworkWindow
from review_homework import save_correction
from test_answers import block, example
from test_api import answer_blocks, api_server, sse
from test_gui import create_archive

ROOT = Path(__file__).resolve().parents[1]
APP = QApplication.instance() or QApplication([])
APP.setStyle('Fusion')


class BulkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.folder = Path(self.temp.name)
        self.rows = example(self.folder)
        (self.folder/'电路作业.mhtml').write_text('placeholder', encoding='utf-8')
        self.config = self.folder/'api.json'
        self.state = self.folder/'state.json'
        self.prompt_config = self.folder/'prompts.json'
        self.profile = new_profile()
        self.profile.update(name='Local test', model='mock', api_key='BULK-TEST-KEY',
                            base_url='http://127.0.0.1:1/v1')
        save_profiles(self.config, [self.profile])
        self.write_cards([(1, 1), (2, 2), (3, 3)])
        self.window = HomeworkWindow(self.folder, self.state, self.config, self.prompt_config)
        self.window._load_export(self.folder)
        self.window._load_cards(self.folder)
        self.window._update_buttons()
        self.window.show()
        QTest.qWait(40)

    def write_cards(self, ranges):
        pages = []
        for index, (first, last) in enumerate(ranges):
            name = f'card{index}.png'
            Image.new('RGB', (200, 160), (50+index*30, 80, 100)).save(self.folder/name)
            pages.append({'image': name, 'first': first, 'last': last, 'question_count': last-first+1,
                          'width': 200, 'height': 160})
        (self.folder/'manifest.json').write_text(json.dumps({'source_file': '电路作业.mhtml',
            'questions_per_image': 1, 'pages': pages}, ensure_ascii=False), encoding='utf-8')

    def tearDown(self):
        if self.window:
            if self.window._bulk_running():
                self.window.start_bulk_api()
            if self.window.job and self.window.job['kind'] == 'api':
                self.window.start_api()
            deadline = time.monotonic()+10
            while self.window.job and time.monotonic() < deadline:
                QTest.qWait(25)
            self.window.close()
            self.window.deleteLater()
            APP.processEvents()
        self.temp.cleanup()

    def wait_until(self, predicate, timeout=20):
        deadline = time.monotonic()+timeout
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(25)
        self.assertTrue(predicate(), self.window.status_label.text()+' / '+self.window.bulk_label.text())

    def selected_rows(self, request):
        text = request['body']['messages'][-1]['content'][0]['text']
        return [row for row in self.rows if row['key'] in text]

    def test_plan_counts_saved_and_corrected_answers_and_rejects_missing_images(self):
        self.write_cards([(1, 2), (3, 3)])
        merge_answers(self.folder, block(self.rows[0], '已完成'))
        save_correction(self.folder, 3, '官方解析', '修正后的解析')
        plan = plan_cards(self.folder, self.folder/'manifest.json')
        self.assertEqual(plan['answered'], [1, 3])
        self.assertEqual(plan['cards'][0]['keep_orders'], [1])
        self.assertFalse(plan['cards'][0]['complete'])
        self.assertTrue(plan['cards'][1]['complete'])
        (self.folder/'card1.png').unlink()
        with self.assertRaisesRegex(ValueError, '图片缺失'):
            plan_cards(self.folder, self.folder/'manifest.json')

    def test_requests_match_single_card_calls_and_save_before_next_request(self):
        release = threading.Event()
        saved_before_next = []
        def reply(request):
            rows = self.selected_rows(request)
            if rows[0]['order'] == 1:
                release.wait(timeout=10)
            else:
                stored = json.loads((self.folder/'answers.json').read_text(encoding='utf-8'))['answers']
                saved_before_next.append(self.rows[rows[0]['order']-2]['key'] in stored)
            return sse(answer_blocks(rows))
        with api_server(reply, 'text/event-stream') as (url, requests):
            try:
                profile = dict(self.profile, base_url=url)
                save_profiles(self.config, [profile])
                self.window.prompt_template_combo.setCurrentIndex(self.window.prompt_template_combo.findData('digital'))
                prompt = self.window._selected_prompt_text()
                self.window.cards.setCurrentRow(2)
                self.window.prompt.setPlainText('第三组自己的草稿')
                self.window.start_bulk_api()
                self.wait_until(lambda: len(requests) == 1)
                config_input = self.window.job['config_input']
                self.assertTrue(config_input.is_file())
                self.assertFalse(self.window.prompt.isReadOnly())
                self.assertEqual(self.window.prompt.toPlainText(), '第三组自己的草稿')
                save_profiles(self.config, [dict(profile, model='changed-after-start')])
                release.set()
                self.wait_until(lambda: self.window.bulk_task is None and self.window.job is None)
                self.assertEqual(len(requests), 3)
                self.assertTrue(all(saved_before_next))
                for index, request in enumerate(requests):
                    expected, _ = build_payload(profile, self.folder, index+1, index+1,
                                                self.folder/f'card{index}.png', prompt_text=prompt)
                    self.assertEqual(request['body']['messages'], expected['messages'])
                    self.assertEqual(request['body']['model'], 'mock')
                self.assertFalse(config_input.exists())
                self.assertEqual(self.window.answered, {1, 2, 3})
                self.assertNotIn('BULK-TEST-KEY', self.state.read_text(encoding='utf-8'))
                self.assertTrue(self.window.open_study_button.isEnabled())
            finally:
                release.set()

    def test_partial_group_preserves_existing_answer_and_skips_corrected_group(self):
        self.write_cards([(1, 2), (3, 3)])
        self.window._load_cards(self.folder)
        merge_answers(self.folder, block(self.rows[0], '保留原先正确的答案'))
        save_correction(self.folder, 3, '官方解析', '官方修正答案')
        with api_server(lambda request: sse(answer_blocks(self.selected_rows(request))), 'text/event-stream') as (url, requests):
            save_profiles(self.config, [dict(self.profile, base_url=url)])
            self.window.start_bulk_api()
            self.wait_until(lambda: self.window.bulk_task is None and self.window.job is None)
            self.assertEqual(len(requests), 1)
            self.assertEqual([row['order'] for row in self.selected_rows(requests[0])], [1, 2])
            stored = json.loads((self.folder/'answers.json').read_text(encoding='utf-8'))['answers']
            self.assertEqual(stored[self.rows[0]['key']], '保留原先正确的答案')
            self.assertIn(self.rows[1]['key'], stored)
            self.assertNotIn(self.rows[2]['key'], stored)
            self.assertIn('跳过已有答案 1 组', self.window.bulk_label.text())

    def test_failure_pauses_and_restart_skips_saved_groups(self):
        failed = []
        def reply(request):
            rows = self.selected_rows(request)
            if rows[0]['order'] == 2 and not failed:
                failed.append(True)
                return sse('不完整的回答')
            return sse(answer_blocks(rows))
        with api_server(reply, 'text/event-stream') as (url, requests):
            save_profiles(self.config, [dict(self.profile, base_url=url)])
            self.window.start_bulk_api()
            self.wait_until(lambda: self.window.bulk_task is not None and not self.window._bulk_running()
                            and self.window.job is None)
            self.assertEqual(len(requests), 2)
            self.assertEqual(self.window.answered, {1})
            QTest.qWait(100)
            self.assertEqual(len(requests), 2)
            self.window.close()
            self.window.deleteLater()
            APP.processEvents()
            self.window = HomeworkWindow(self.folder, self.state, self.config, self.prompt_config)
            self.window.show()
            self.window.start_bulk_api()
            self.wait_until(lambda: self.window.bulk_task is None and self.window.job is None)
            self.assertEqual(len(requests), 4)
            self.assertEqual([self.selected_rows(r)[0]['order'] for r in requests], [1, 2, 2, 3])

    def test_stop_cancels_active_request_and_preserves_other_card_draft(self):
        release = threading.Event()
        def reply(request):
            rows = self.selected_rows(request)
            if rows[0]['order'] == 2:
                release.wait(timeout=10)
            return sse(answer_blocks(rows))
        with api_server(reply, 'text/event-stream') as (url, requests):
            try:
                save_profiles(self.config, [dict(self.profile, base_url=url)])
                self.window.card_texts['2-2'] = '第二组原草稿'
                self.window.start_bulk_api()
                self.wait_until(lambda: len(requests) == 2)
                self.window.cards.setCurrentRow(1)
                self.assertTrue(self.window.prompt.isReadOnly())
                self.window.cards.setCurrentRow(2)
                self.window.prompt.setPlainText('第三组新草稿')
                QTest.mouseClick(self.window.bulk_api_button, Qt.MouseButton.LeftButton)
                self.wait_until(lambda: self.window.job is None)
                self.assertFalse(self.window._bulk_running())
                self.assertEqual(self.window.card_texts['2-2'], '第二组原草稿')
                self.assertEqual(self.window.prompt.toPlainText(), '第三组新草稿')
                self.assertEqual(self.window.answered, {1})
                release.set()
                QTest.qWait(100)
                self.assertEqual(len(requests), 2)
                QTest.mouseClick(self.window.bulk_api_button, Qt.MouseButton.LeftButton)
                self.wait_until(lambda: self.window.bulk_task is None and self.window.job is None)
                self.assertEqual([self.selected_rows(r)[0]['order'] for r in requests], [1, 2, 2, 3])
            finally:
                release.set()

    def test_edits_to_other_groups_are_saved_and_active_api_keeps_priority(self):
        release = threading.Event()
        def reply(request):
            rows = self.selected_rows(request)
            if rows[0]['order'] == 1:
                release.wait(timeout=10)
            return sse(answer_blocks(rows))
        with api_server(reply, 'text/event-stream') as (url, requests):
            try:
                save_profiles(self.config, [dict(self.profile, base_url=url)])
                self.window.start_bulk_api()
                self.wait_until(lambda: len(requests) == 1)
                self.window.cards.setCurrentRow(2)
                self.window.prompt.setPlainText(block(self.rows[0], '不应覆盖 API 的结果')+'\n\n'
                                                +block(self.rows[2], '第三题的手工答案'))
                QTest.qWait(850)
                release.set()
                self.wait_until(lambda: self.window.bulk_task is None and self.window.job is None)
                stored = json.loads((self.folder/'answers.json').read_text(encoding='utf-8'))['answers']
                self.assertNotIn('不应覆盖', stored[self.rows[0]['key']])
                self.assertEqual(stored[self.rows[2]['key']], '第三题的手工答案')
                self.assertEqual([self.selected_rows(r)[0]['order'] for r in requests], [1, 2])
            finally:
                release.set()

    def test_merge_failure_retries_saving_without_repeating_api_request(self):
        injected = []
        handle = self.window._api_event
        def record(event):
            if event.get('type') == 'result' and event.get('valid') and not injected:
                (self.folder/'answers.json').mkdir()
                injected.append(True)
            handle(event)
        self.window._api_event = record
        with api_server(lambda request: sse(answer_blocks(self.selected_rows(request))), 'text/event-stream') as (url, requests):
            save_profiles(self.config, [dict(self.profile, base_url=url)])
            self.window.start_bulk_api()
            self.wait_until(lambda: self.window.bulk_task is not None and not self.window._bulk_running()
                            and self.window.job is None)
            self.assertEqual(len(requests), 1)
            self.assertIsNotNone(self.window.bulk_task['pending_result'])
            (self.folder/'answers.json').rmdir()
            self.window.start_bulk_api()
            self.wait_until(lambda: self.window.bulk_task is None and self.window.job is None)
            self.assertEqual(len(requests), 3)
            self.assertEqual(self.window.answered, {1, 2, 3})

    def test_automatic_conversion_and_render_before_requests(self):
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()
        raw = self.folder/'raw'
        raw.mkdir()
        create_archive(raw)
        def reply(request):
            rows = load_questions(self.window.export_dir)[2]
            text = request['body']['messages'][-1]['content'][0]['text']
            return sse(answer_blocks([row for row in rows if row['key'] in text]))
        with api_server(reply, 'text/event-stream') as (url, requests):
            save_profiles(self.config, [dict(self.profile, base_url=url)])
            self.window = HomeworkWindow(raw, raw/'state.json', self.config, self.prompt_config)
            self.window.n_spin.setValue(2)
            self.window.show()
            self.assertIsNone(self.window.export_dir)
            self.window.start_bulk_api()
            self.wait_until(lambda: self.window.bulk_task is None and self.window.job is None, timeout=35)
            self.assertEqual(len(requests), 2)
            self.assertEqual(self.window.answered, {1, 2, 3})
            self.assertTrue((self.window.export_dir/'questions.md').is_file())
            self.assertEqual(self.window.cards.count(), 2)


if __name__ == '__main__':
    unittest.main()
