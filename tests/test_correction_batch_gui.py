"""Correction import and sequential API integration, using only a local fake service."""
import json
import sys
import threading
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from api_config import save_profiles
from review_homework import question_detail, save_correction
from test_api import answer_blocks, api_server, sse
import test_api_gui
from test_extract import archive
from test_official_correction import feedback


class CorrectionBatchGUITests(unittest.TestCase):
    setUp = test_api_gui.APIGUITests.setUp
    tearDown = test_api_gui.APIGUITests.tearDown
    wait_until = test_api_gui.APIGUITests.wait_until

    def activate_panel(self):
        panel = self.window.correction_panel
        self.window.tabs.setCurrentWidget(panel)
        self.wait_until(lambda: panel.detail is not None and panel.job is None and not panel.queue)
        return panel

    def configure(self, url):
        save_profiles(self.config, [dict(self.profile, base_url=url, send_image=False)])
        self.window._refresh_api_profiles()

    def reply(self, request):
        prompt = request['body']['messages'][-1]['content']
        row = next(row for row in self.rows if row['key'] in prompt)
        return sse(answer_blocks([row]))

    def test_import_only_fills_notes_then_batch_includes_unvisited_question(self):
        result = self.folder/'result.mhtml'
        archive(result, feedback('a', 'marking_dui')+feedback('b', answer='错误', analysis='官方解释'))
        panel = self.activate_panel()
        panel.correction_note.setText('第一题手工建议')
        with api_server(self.reply, 'text/event-stream') as (url, requests):
            self.configure(url)
            panel.result_path.setText(str(result))
            QTest.mouseClick(panel.import_results_button, Qt.LeftButton)
            self.wait_until(lambda: not panel.import_pending and panel.job is None and not panel.queue)
            self.assertFalse(requests)
            self.assertIsNone(panel.api_job)
            self.assertIn('未调用 AI', self.window.status_label.text())
            self.assertIn('官方解释', question_detail(self.folder, 2)['correction_note'])
            QTest.mouseClick(panel.bulk_correction_api_button, Qt.LeftButton)
            self.wait_until(lambda: panel.correction_batch is None and panel.api_job is None and panel.job is None)
            self.assertEqual(len(requests), 2)
            for index, request in enumerate(requests):
                self.assertIn(self.rows[index]['key'], request['body']['messages'][-1]['content'])
                self.assertTrue(question_detail(self.folder, index+1)['corrected_answer'])
            self.assertIn('已保存 2 题', panel.correction_bulk_label.text())
            self.assertFalse((self.folder/'answers.json').exists())

    def test_invalid_reply_pauses_and_retry_continues_without_repeating_saved_question(self):
        for order in (1, 2):
            save_correction(self.folder, order, f'建议 {order}', f'旧修正 {order}')
        panel = self.activate_panel()
        def reply(request):
            return sse('缺少题目标记') if len(requests) == 2 else self.reply(request)
        with api_server(reply, 'text/event-stream') as (url, requests):
            self.configure(url)
            panel.start_bulk_correction_api()
            self.wait_until(lambda: len(requests) == 2 and not panel.is_busy())
            self.assertEqual(panel.correction_batch['position'], 1)
            self.assertEqual(question_detail(self.folder, 2)['corrected_answer'], '旧修正 2')
            self.assertIn('暂停', panel.correction_bulk_label.text())
            panel.retry_correction_api()
            self.wait_until(lambda: len(requests) == 3 and panel.correction_batch is None and panel.job is None)
            self.assertIn(self.rows[1]['key'], requests[2]['body']['messages'][-1]['content'])
            self.assertIn('结论', question_detail(self.folder, 2)['corrected_answer'])

    def test_save_failure_retries_storage_without_another_api_call(self):
        save_correction(self.folder, 1, '建议', '旧修正')
        panel = self.activate_panel()
        original = panel.command_factory
        failed = []
        def command(script, arguments):
            if script == 'review_correction.py' and arguments[0] == 'save' and not failed:
                failed.append(True)
                return sys.executable, ['-c', 'import json; print(json.dumps({"message":"模拟磁盘保存失败"})); raise SystemExit(1)']
            return original(script, arguments)
        with api_server(self.reply, 'text/event-stream') as (url, requests):
            self.configure(url)
            with patch.object(panel, 'command_factory', command):
                panel.start_bulk_correction_api()
                self.wait_until(lambda: failed and not panel.is_busy() and panel.job is None)
                self.assertIsNotNone(panel.correction_batch['waiting'])
                self.assertEqual(question_detail(self.folder, 1)['corrected_answer'], '旧修正')
                self.assertTrue(panel.correction_answer.isReadOnly())
                panel.start_bulk_correction_api()
                self.wait_until(lambda: panel.correction_batch is None and panel.job is None and not panel.queue)
            self.assertEqual(len(requests), 1)
            self.assertIn('结论', question_detail(self.folder, 1)['corrected_answer'])

    def test_stop_then_continue_preserves_old_answer_and_current_position(self):
        save_correction(self.folder, 1, '建议', '保留的原修正')
        panel = self.activate_panel()
        release = threading.Event()
        def reply(request):
            if len(requests) == 1:
                release.wait(10)
            return self.reply(request)
        with api_server(reply, 'text/event-stream') as (url, requests):
            try:
                self.configure(url)
                panel.start_bulk_correction_api()
                self.wait_until(lambda: len(requests) == 1)
                self.assertFalse(self.window.call_api_button.isEnabled())
                self.window.tabs.setCurrentWidget(self.window.review_panel)
                self.window.tabs.setCurrentWidget(panel)
                panel.start_bulk_correction_api()
                self.wait_until(lambda: panel.api_job is None)
                self.assertEqual(panel.correction_batch['position'], 0)
                self.assertEqual(question_detail(self.folder, 1)['corrected_answer'], '保留的原修正')
                release.set()
                panel.start_bulk_correction_api()
                self.wait_until(lambda: panel.correction_batch is None and panel.job is None)
                self.assertEqual(len(requests), 2)
                self.assertIn('结论', question_detail(self.folder, 1)['corrected_answer'])
            finally:
                release.set()

    def test_correction_reasoning_retry_displays_query_success_and_failure(self):
        panel = self.activate_panel()
        catalog = json.dumps({'data': [{'id': self.profile['model'],
                                      'reasoning': {'supported_efforts': ['low', 'high']}}]})
        with api_server('{}', catalog=catalog) as (url, requests):
            self.configure(url)
            self.wait_until(lambda: self.window.reasoning_process is None)
            QTest.mouseClick(panel.retry_review_reasoning_button, Qt.LeftButton)
            self.assertEqual(panel.review_reasoning_hint.text(), '查询档位中…')
            self.wait_until(lambda: self.window.reasoning_process is None)
            self.assertEqual(panel.review_reasoning_hint.text(), '2 档可选')
            self.assertIn('获取思考强度成功', self.window.status_label.text())
            self.assertFalse(requests)
        blocked = json.dumps({'cloudflare_error': True, 'error_code': 1010})
        with api_server(blocked, status=403, catalog=(403, blocked)) as (url, _):
            self.configure(url)
            self.wait_until(lambda: self.window.reasoning_process is None)
            QTest.mouseClick(panel.retry_review_reasoning_button, Qt.LeftButton)
            self.wait_until(lambda: self.window.reasoning_process is None)
            self.assertEqual(panel.review_reasoning_hint.text(), '服务商拦截查询')
            self.assertIn('Cloudflare', self.window.status_label.text())


if __name__ == '__main__':
    unittest.main()
