import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QPushButton

from homework_gui import HomeworkWindow, scan_mhtml
from answers_homework import load_questions
from cli_progress import PROGRESS_PREFIX


ROOT = Path(__file__).resolve().parents[1]
APP = QApplication.instance() or QApplication([])
APP.setStyle('Fusion')


def create_archive(folder):
    image_path = folder/'diagram.png'
    Image.new('RGB', (600, 1600), '#d32929').save(image_path)
    message = EmailMessage()
    message.make_related()
    part = EmailMessage()
    part.set_content('''<h3>测试作业</h3>
        <div class="singleQuesId" data="q1"><div class="Zy_TItle"><i>1</i>
          <div class="fontLabel"><span class="newZy_TItle">【单选题】</span>
          第一题有一张很高的图<img src="cid:diagram"></div></div></div>
        <div class="singleQuesId" data="q2"><div class="Zy_TItle"><i>2</i>
          <div class="fontLabel"><span class="newZy_TItle">【判断题】</span>第二题</div></div></div>
        <div class="singleQuesId" data="q3"><div class="Zy_TItle"><i>3</i>
          <div class="fontLabel"><span class="newZy_TItle">【判断题】</span>第三题</div></div></div>
        ''', subtype='html', charset='utf-8')
    message.attach(part)
    part = EmailMessage()
    part.set_content(image_path.read_bytes(), maintype='image', subtype='png')
    part['Content-ID'] = '<diagram>'
    message.attach(part)
    source = folder/'中文作业.mhtml'
    source.write_bytes(message.as_bytes())
    return source


class GUIInteractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.folder = Path(self.temp.name)
        self.state_path = self.folder/'state.json'
        self.window = None

    def tearDown(self):
        if self.window:
            self.window.close()
            self.window.deleteLater()
            APP.processEvents()
        self.temp.cleanup()

    def show_window(self):
        self.window = HomeworkWindow(self.folder, self.state_path)
        self.window.show()
        QTest.qWait(80)

    def wait_for_job(self, timeout=30):
        deadline = time.monotonic()+timeout
        while self.window.job is not None and time.monotonic() < deadline:
            QTest.qWait(40)
        self.assertIsNone(self.window.job, self.window.log.decode('utf-8', errors='replace'))

    def test_scan_refresh_and_empty_directory(self):
        create_archive(self.folder)
        (self.folder/'ignored.txt').write_text('not an archive')
        self.show_window()
        self.assertEqual(self.window.file_list.count(), 1)
        self.assertTrue(self.window.convert_button.isEnabled())
        self.assertFalse(self.window.render_button.isEnabled())
        selected = self.window.source
        (self.folder/'new.MHT').write_text('test')
        QTest.mouseClick(self.window.refresh_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.file_list.count(), 2)
        self.assertEqual(self.window.source, selected)
        for source in scan_mhtml(self.folder):
            source.unlink()
        self.window.refresh_files()
        self.assertEqual(self.window.file_list.count(), 0)
        self.assertFalse(self.window.convert_button.isEnabled())
        self.assertFalse(self.window.prompt.isEnabled())

    def test_rename_mhtml_keeps_existing_export_and_rejects_collision(self):
        source = create_archive(self.folder)
        (self.folder/'已存在.mhtml').write_bytes(source.read_bytes())
        self.show_window()
        for index in range(self.window.file_list.count()):
            if self.window.file_list.item(index).text() == source.name:
                self.window.file_list.setCurrentRow(index)
                break
        self.window.start_convert()
        self.wait_for_job()
        export = self.window.export_dir
        self.assertIsNotNone(export)

        QTest.mouseClick(self.window.title_label, Qt.MouseButton.LeftButton)
        self.assertTrue(self.window.rename_input.isVisible())
        self.assertEqual(self.window.rename_input.text(), source.stem)
        self.assertTrue(self.window.rename_confirm_button.isVisible())
        self.assertFalse(self.window.title_label.isVisible())
        self.window.rename_input.setText('新的作业')
        QTest.mouseClick(self.window.rename_confirm_button, Qt.MouseButton.LeftButton)
        renamed = self.folder/'新的作业.mhtml'
        self.assertTrue(renamed.is_file())
        self.assertFalse(source.exists())
        self.assertEqual(self.window.source, renamed)
        self.assertEqual(self.window.title_label.text(), renamed.stem)
        self.assertEqual(self.window.file_list.currentItem().text(), renamed.name)
        self.assertEqual(self.window.export_dir, export)
        self.assertEqual(self.window.report['source_file'], source.name)
        saved = json.loads(self.state_path.read_text(encoding='utf-8'))
        self.assertEqual(saved['selected_source'], str(renamed))
        self.assertNotIn(str(source), saved['files'])
        self.assertEqual(saved['files'][str(renamed)]['source_file'], source.name)

        QTest.mouseClick(self.window.title_label, Qt.MouseButton.LeftButton)
        self.window.rename_input.setText('已存在')
        QTest.mouseClick(self.window.rename_confirm_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.source, renamed)
        self.assertIn('同名文件已存在', self.window.status_label.text())
        QTest.keyClick(self.window.rename_input, Qt.Key.Key_Escape)
        self.assertFalse(self.window.rename_input.isVisible())

        self.window.close()
        self.window.deleteLater()
        APP.processEvents()
        self.window = None
        self.show_window()
        self.assertEqual(self.window.source, renamed)
        self.assertEqual(self.window.export_dir, export)
        self.window.start_convert()
        self.wait_for_job()
        self.assertEqual(self.window.report['source_file'], renamed.name)

    def test_shared_render_settings_across_files_folders_and_restart(self):
        for name in ('a.mhtml', 'b.mhtml'):
            (self.folder/name).write_text('test')
        self.show_window()
        self.window.height_mode.setChecked(True)
        self.window.n_spin.setValue(7)
        self.window.height_spin.setValue(3200)
        self.window.width_spin.setValue(1440)
        self.assertEqual(self.window.ocr_model_combo.currentData(), 'v4')
        self.window.ocr_model_combo.setCurrentIndex(1)
        self.window.prompt.setPlainText('A 文件的输入')
        # Switch immediately, before the debounced settings save runs.
        self.window.file_list.setCurrentRow(1)
        self.assertTrue(self.window.height_mode.isChecked())
        self.assertEqual((self.window.n_spin.value(), self.window.height_spin.value(),
                          self.window.width_spin.value()), (7, 3200, 1440))
        self.assertEqual(self.window.prompt.toPlainText(), '')
        self.assertEqual(self.window.ocr_model_combo.currentData(), 'v6tiny')
        self.window.count_mode.setChecked(True)
        self.window.n_spin.setValue(11)
        self.window.height_spin.setValue(4600)
        self.window.width_spin.setValue(1600)
        self.window.prompt.setPlainText('B 文件的输入')
        self.window.file_list.setCurrentRow(0)
        expected = {'mode': 'count', 'n': 11, 'max_height': 4600, 'width': 1600}
        self.assertEqual(self.window._render_settings(), expected)
        self.assertEqual(self.window.prompt.toPlainText(), 'A 文件的输入')
        self.assertEqual(self.window.ocr_model_combo.currentData(), 'v6tiny')

        other = self.folder/'other'
        other.mkdir()
        with patch('homework_gui.QFileDialog.getExistingDirectory', return_value=str(other)):
            self.window.choose_folder()
        self.assertIsNone(self.window.source)
        self.assertEqual(self.window._render_settings(), expected)
        (other/'c.mhtml').write_text('test')
        self.window.refresh_files()
        self.assertEqual(self.window.source, (other/'c.mhtml').resolve())
        self.assertEqual(self.window._render_settings(), expected)
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()
        self.show_window()
        self.assertEqual(self.window._render_settings(), expected)
        self.assertTrue(self.window.n_spin.isEnabled())
        self.assertEqual(self.window.ocr_model_combo.currentData(), 'v6tiny')
        self.assertFalse(self.window.height_spin.isEnabled())
        stored = json.loads(self.state_path.read_text(encoding='utf-8'))
        self.assertEqual(stored['render_settings'], expected)
        self.assertEqual(stored['ocr_model'], 'v6tiny')
        self.assertEqual(stored['files'][str((self.folder/'b.mhtml').resolve())]['text'], 'B 文件的输入')
        for entry in stored['files'].values():
            self.assertTrue({'mode', 'n', 'max_height', 'width'}.isdisjoint(entry))

    def test_legacy_settings_migrate_from_last_selected_file(self):
        a, b = (self.folder/'a.mhtml').resolve(), (self.folder/'b.mhtml').resolve()
        a.write_text('test')
        b.write_text('test')
        selected = {'mode': 'height', 'n': 8, 'max_height': 3800, 'width': 1440}
        state = {'selected_source': str(b), 'files': {
            str(a): {'mode': 'count', 'n': 2, 'max_height': 1000, 'width': 960, 'text': '旧输入 A'},
            str(b): {**selected, 'text': '旧输入 B'},
        }}
        self.state_path.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        self.show_window()
        self.assertEqual(self.window.source, b)
        self.assertEqual(self.window._render_settings(), selected)
        self.assertEqual(self.window.prompt.toPlainText(), '旧输入 B')
        self.window.file_list.setCurrentRow(0)
        self.assertEqual(self.window._render_settings(), selected)
        self.assertEqual(self.window.prompt.toPlainText(), '旧输入 A')
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()
        stored = json.loads(self.state_path.read_text(encoding='utf-8'))
        self.assertEqual(stored['render_settings'], selected)
        for entry in stored['files'].values():
            self.assertTrue({'mode', 'n', 'max_height', 'width'}.isdisjoint(entry))
        # An existing global configuration always wins over stale file entries.
        shared = {'mode': 'count', 'n': 12, 'max_height': 4200, 'width': 1280}
        stored['render_settings'] = shared
        stored['files'][str(a)].update(selected)
        self.state_path.write_text(json.dumps(stored, ensure_ascii=False), encoding='utf-8')
        self.show_window()
        self.assertEqual(self.window._render_settings(), shared)

    def test_progress_stream_handles_split_utf8_lines_and_stages(self):
        create_archive(self.folder)
        self.show_window()
        self.window.job = {'kind': 'convert'}
        self.window.progress.setRange(0, 0)
        self.window.progress.show()
        event = {'stage': 'questions', 'current': 2, 'total': 10, 'message': '正在识别第 3/10 题'}
        encoded = (PROGRESS_PREFIX + json.dumps(event, ensure_ascii=False) + '\n').encode('utf-8')
        split = encoded.index('正在'.encode('utf-8')) + 1
        try:
            self.window._consume_output(encoded[:split])
            self.assertEqual(self.window.progress.maximum(), 0)
            self.window._consume_output(encoded[split:])
            self.assertEqual(self.window.status_label.text(), event['message'])
            self.assertEqual((self.window.progress.value(), self.window.progress.maximum()), (2, 10))
            self.assertTrue(self.window.progress.isTextVisible())
            invalid = (PROGRESS_PREFIX + '{invalid}\n').encode('utf-8')
            self.window._consume_output(b'ordinary log\n' + invalid)
            self.assertEqual(self.window.progress.value(), 2)
            event = {'stage': 'render', 'current': 1, 'total': 3, 'message': '已生成 1/3 张图片'}
            next_line = (PROGRESS_PREFIX + json.dumps(event, ensure_ascii=False) + '\r\n').encode('utf-8')
            self.window._consume_output(next_line)
            self.assertEqual((self.window.progress.value(), self.window.progress.maximum()), (1, 3))
            self.assertEqual(self.window.log, encoded + b'ordinary log\n' + invalid + next_line)
            event = {'stage': 'browser_close', 'current': 0, 'total': 0, 'message': '正在关闭渲染浏览器'}
            self.window._consume_output((PROGRESS_PREFIX + json.dumps(event, ensure_ascii=False) + '\n').encode('utf-8'))
            self.assertEqual(self.window.progress.maximum(), 0)
            self.assertEqual(self.window.status_label.text(), event['message'])
            self.assertFalse(self.window.progress.isTextVisible())
        finally:
            self.window.job = None

    @unittest.skipUnless(os.name == 'nt', 'Real local OCR/browser CLI processes')
    def test_real_cli_conversion_height_render_clipboard_and_persistence(self):
        create_archive(self.folder)
        self.show_window()
        self.window.ocr_model_combo.setCurrentIndex(1)
        progress_events = []
        update = self.window._update_progress
        def record_progress(event):
            progress_events.append(event)
            update(event)
        self.window._update_progress = record_progress
        QTest.mouseClick(self.window.convert_button, Qt.MouseButton.LeftButton)
        self.assertIsNotNone(self.window.job)
        self.assertFalse(self.window.convert_button.isEnabled())
        self.assertFalse(self.window.ocr_model_combo.isEnabled())
        args = self.window.process.arguments()
        self.assertEqual(args[args.index('--ocr-model')+1], 'v6tiny')
        self.assertIn('--progress-json', args)
        self.assertIn(str(ROOT/'extract_homework.py'), self.window.process.arguments())
        self.wait_for_job()
        self.assertIsNotNone(self.window.export_dir, self.window.status_label.text())
        self.assertEqual(self.window.report['question_count'], 3)
        self.assertEqual(self.window.report['ocr']['model'], 'v6tiny')
        self.assertTrue(any(e['stage'] == 'questions' and e['current'] == 1 and e['total'] == 3
                            for e in progress_events))
        self.assertFalse(self.window.progress.isVisible())
        self.assertTrue(self.window.render_button.isEnabled())
        self.window.height_mode.setChecked(True)
        self.window.height_spin.setValue(1000)
        self.window.width_spin.setValue(960)
        QTest.mouseClick(self.window.render_button, Qt.MouseButton.LeftButton)
        self.assertIn('--max-height', self.window.process.arguments())
        self.assertIn(str(ROOT/'render_homework.py'), self.window.process.arguments())
        self.wait_for_job()
        self.assertTrue(any(e['stage'] == 'measure' and e['current'] == 1 and e['total'] == 3
                            for e in progress_events))
        self.assertTrue(any(e['stage'] == 'render' and e['current'] == 1 and e['total'] == 2
                            for e in progress_events))
        self.assertTrue(any(e['stage'] == 'browser_close' for e in progress_events))
        self.assertEqual(self.window.cards.count(), 2, self.window.status_label.text())
        manifest = json.loads((self.window.render_dir/'manifest.json').read_text(encoding='utf-8'))
        self.assertTrue(manifest['pages'][0]['oversize'])
        self.assertEqual(manifest['pages'][1]['question_count'], 2)
        self.window.prompt.setPlainText('请先给思路，再给步骤。')
        APP.clipboard().setText('保留剪贴板文字')
        other = self.window.cards.item(1)
        other_rect = self.window.cards.visualItemRect(other)
        QTest.mouseClick(self.window.cards.viewport(), Qt.MouseButton.LeftButton,
                         pos=QPoint(other_rect.center().x(), other_rect.bottom()-25))
        self.assertEqual(self.window.cards.currentRow(), 1)
        self.assertEqual(APP.clipboard().text(), '保留剪贴板文字')
        item = self.window.cards.item(0)
        APP.processEvents()
        QTest.mouseClick(self.window.cards.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.window.cards.visualItemRect(item).center())
        mime = APP.clipboard().mimeData()
        self.assertTrue(mime.hasImage())
        copied = mime.imageData()
        self.assertEqual((copied.width(), copied.height()),
                         (manifest['pages'][0]['width'], manifest['pages'][0]['height']))
        original = QImage(item.data(Qt.ItemDataRole.UserRole))
        self.assertEqual(copied.pixelColor(100, 100), original.pixelColor(100, 100))
        self.assertEqual(self.window.prompt.toPlainText(), '请先给思路，再给步骤。')
        with patch('homework_gui.QDesktopServices.openUrl', return_value=True) as open_url:
            QTest.mouseClick(self.window.open_markdown_button, Qt.MouseButton.LeftButton)
            self.assertEqual(Path(open_url.call_args[0][0].toLocalFile()), self.window.export_dir/'questions.md')
            QTest.mouseClick(self.window.open_folder_button, Qt.MouseButton.LeftButton)
            self.assertEqual(Path(open_url.call_args[0][0].toLocalFile()), self.window.export_dir)
        old_export = self.window.export_dir
        old_markdown = (old_export/'questions.md').read_bytes()
        # Re-conversion uses a new CLI output root, preserving old Markdown/notes.
        QTest.mouseClick(self.window.convert_button, Qt.MouseButton.LeftButton)
        self.wait_for_job()
        self.assertNotEqual(self.window.export_dir, old_export)
        self.assertEqual((old_export/'questions.md').read_bytes(), old_markdown)
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()
        self.show_window()
        self.assertEqual(self.window.prompt.toPlainText(), '请先给思路，再给步骤。')
        self.assertTrue(self.window.height_mode.isChecked())
        self.assertEqual(self.window.height_spin.value(), 1000)
        self.assertEqual(self.window.ocr_model_combo.currentData(), 'v6tiny')

    def test_cli_failure_recovers_buttons_and_shows_error(self):
        (self.folder/'bad.mhtml').write_text('invalid archive')
        self.show_window()
        QTest.mouseClick(self.window.convert_button, Qt.MouseButton.LeftButton)
        self.wait_for_job()
        self.assertTrue(self.window.convert_button.isEnabled())
        self.assertFalse(self.window.render_button.isEnabled())
        self.assertIn('失败', self.window.status_label.text())
        self.assertIsNone(self.window.export_dir)
        self.assertFalse(list(self.folder.rglob('.extract-*')))

    def test_answer_prompt_and_automatic_markdown_merge_through_cli(self):
        create_archive(self.folder)
        self.show_window()
        self.window.start_convert()
        self.wait_for_job()
        # Small local thumbnails suffice; image rendering has its own real CLI test.
        directory = self.window.export_dir/'rendered'
        directory.mkdir()
        pages = []
        for first, last in ((1, 1), (2, 3)):
            filename = f'{first}.png'
            Image.new('RGB', (120, 180), 'white').save(directory/filename)
            pages.append({'image': filename, 'first': first, 'last': last,
                          'question_count': last-first+1, 'height': 180, 'width': 120})
        (directory/'manifest.json').write_text(json.dumps({
            'source_file': self.window.source.name, 'questions_per_image': 2, 'pages': pages}), encoding='utf-8')
        self.window._load_cards(directory)
        self.window._update_buttons()
        self.window.cards.setCurrentRow(1)
        self.window.prompt_template_combo.setCurrentIndex(self.window.prompt_template_combo.findData('digital'))
        QTest.mouseClick(self.window.copy_prompt_button, Qt.MouseButton.LeftButton)
        self.assertIn(str(ROOT/'answers_homework.py'), self.window.process.arguments())
        self.wait_for_job()
        rows = load_questions(self.window.export_dir)[2]
        copied = APP.clipboard().text()
        self.assertIn('格雷码', copied)
        self.assertIn(r'$\overline{C}\,\overline{D}$', copied)
        self.assertIn(rows[1]['key'], copied)
        self.assertIn(rows[2]['key'], copied)
        self.assertNotIn(rows[0]['key'], copied)
        def answer(row, text):
            return f'## 【解答 {row["key"]}】\n{text}\n【结束 {row["key"]}】'
        self.window.prompt.setPlainText(answer(rows[0], '第一题测试解答 $I=U/R$'))
        QTest.qWait(900)
        self.wait_for_job()
        self.assertEqual(self.window.answered, {1})
        self.assertTrue(self.window.open_study_button.isEnabled())
        # Edit while an import is in flight: the newest paste must also be merged.
        self.window.prompt.setPlainText(answer(rows[1], '第二题测试解答'))
        self.window.start_merge()
        self.assertEqual(self.window.job['kind'], 'merge')
        self.window.prompt.setPlainText(answer(rows[2], '第三题测试解答'))
        self.wait_for_job()
        self.assertEqual(self.window.answered, {1, 2, 3})
        document = self.window.study_path.read_text(encoding='utf-8')
        self.assertIn('第一题测试解答 $I=U/R$', document)
        self.assertIn('第二题测试解答', document)
        self.assertIn('第三题测试解答', document)
        self.assertIn('解答 2/2', self.window.cards.item(1).text())
        # A bad paste preserves the compiled document and previous answers.
        self.window.prompt.setPlainText(f'## 【解答 {rows[0]["key"]}】\n缺少结束标记')
        self.window.start_merge()
        self.wait_for_job()
        self.assertIn('缺少结束标记', self.window.status_label.text())
        self.assertEqual(self.window.study_path.read_text(encoding='utf-8'), document)
        with patch('homework_gui.QDesktopServices.openUrl', return_value=True) as opened:
            QTest.mouseClick(self.window.open_study_button, Qt.MouseButton.LeftButton)
            self.assertEqual(Path(opened.call_args[0][0].toLocalFile()), self.window.study_path)
        self.window.prompt.clear()
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()
        self.show_window()
        self.assertEqual(self.window.answered, {1, 2, 3})
        self.assertTrue(self.window.open_study_button.isEnabled())

    def test_image_clipboard_process_exits_cleanly(self):
        image_path = self.folder/'clipboard.png'
        Image.new('RGB', (120, 180), 'red').save(image_path)
        code = '''
import os, sys
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
if os.name == 'nt':
    import ctypes
    ctypes.windll.kernel32.SetErrorMode(3)  # Fail fast instead of showing a crash dialog.
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidgetItem
from homework_gui import HomeworkWindow
app = QApplication([])
image_path = Path(sys.argv[1])
window = HomeworkWindow(image_path.parent, image_path.parent/'child-state.json')
item = QListWidgetItem('第 1–1 题')
item.setData(Qt.ItemDataRole.UserRole, str(image_path))
window.copy_card(item)
assert app.clipboard().mimeData().hasImage()
window.close()
print('Copied full image and shutting down normally', flush=True)
'''
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-X', 'faulthandler',
                                 '-c', code, str(image_path)], cwd=ROOT, capture_output=True,
                                text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout+'\n'+result.stderr)

    def test_review_tabs_navigation_favorites_copy_and_live_preview(self):
        from answers_homework import merge_answers
        from review_homework import save_correction
        create_archive(self.folder)
        self.show_window()
        self.assertEqual([self.window.tabs.tabText(i) for i in range(3)],
                         ['资料制作', '纠错补充', '复习提问'])
        self.assertNotIn('复制文字', [b.text() for b in self.window.findChildren(QPushButton)])
        self.assertNotIn('总设置', [b.text() for b in self.window.findChildren(QPushButton)])
        self.window.start_convert()
        self.wait_for_job()
        rows = load_questions(self.window.export_dir)[2]
        merge_answers(self.window.export_dir,
                      f'## 【解答 {rows[0]["key"]}】\n**结论**\n\n选 A（电流流过电阻）\n\n'
                      f'测试公式 $I=U/R$\n\\[\nU_{{ab}}=-6\\,\\text{{V}}\n\\]\n【结束 {rows[0]["key"]}】')
        self.window.tabs.setCurrentWidget(self.window.review_panel)
        panel = self.window.review_panel
        def wait_review():
            deadline = time.monotonic()+20
            while (panel.job or panel.queue or not panel.detail or not panel.question_view.ready) and time.monotonic() < deadline:
                QTest.qWait(40)
            self.assertIsNone(panel.job)
            self.assertIsNotNone(panel.detail, self.window.status_label.text())
            self.assertTrue(panel.question_view.ready, panel.question_view.load_error)
        wait_review()
        self.assertEqual(panel.question_list.count(), 3)
        summary = panel.question_list.itemWidget(panel.question_list.item(0)).findChild(
            type(panel.question_title), 'reviewQuestionSummary')
        self.assertIn('选 A（电流流过电阻）', summary.full_text)
        self.assertTrue(summary.text().startswith('第 1 题'))
        self.assertLessEqual(summary.fontMetrics().horizontalAdvance(summary.text()), summary.width()+2)
        self.assertEqual(panel.detail['order'], 1)
        self.assertFalse(panel.correction_note.isVisible())
        self.assertTrue(panel.copy_button.isVisible())
        self.assertFalse(any(section.get('label') in {'官方答案与解析', '综合修正解析'}
                             for section in panel.question_view.sections))

        self.window.tabs.setCurrentWidget(self.window.correction_panel)
        correction = self.window.correction_panel
        deadline = time.monotonic()+20
        while (correction.job or correction.queue or not correction.detail) and time.monotonic() < deadline:
            QTest.qWait(40)
        self.assertIsNotNone(correction.detail)
        self.assertEqual(correction.question_list.count(), 3)
        self.assertTrue(correction.correction_note.isVisible())
        self.assertFalse(correction.favorites_only.isVisible())
        self.assertFalse(correction.copy_button.isVisible())
        self.assertIsNone(correction.question_list.itemWidget(correction.question_list.item(0)).findChild(QPushButton))
        correction.question_list.setCurrentRow(1)
        self.window.tabs.setCurrentWidget(self.window.review_panel)
        wait_review()
        self.assertEqual(panel.detail['order'], 1)
        self.assertFalse(panel.previous_button.isEnabled())
        self.assertTrue(panel.next_button.isEnabled())
        self.assertFalse(hasattr(panel, 'chat_input'))
        values = []
        panel.question_view.page().runJavaScript(
            'JSON.stringify({math:document.querySelectorAll(".katex").length, display:document.querySelectorAll(".katex-display").length, images:document.images.length, text:document.body.innerText})',
            lambda value: values.append(value))
        deadline = time.monotonic()+10
        while not values and time.monotonic() < deadline:
            QTest.qWait(40)
        self.assertTrue(values)
        values[0] = json.loads(values[0])
        self.assertEqual(values[0]['math'], 2)
        self.assertEqual(values[0]['display'], 1)
        self.assertEqual(values[0]['images'], 1)
        self.assertIn('测试公式', values[0]['text'])
        QTest.mouseClick(panel.copy_button, Qt.MouseButton.LeftButton)
        wait_review()
        copied = APP.clipboard().text()
        self.assertIn('第一题有一张很高的图', copied)
        self.assertIn('测试公式 $I=U/R$', copied)
        self.assertIn('file:///', copied)
        QTest.mouseClick(panel.next_button, Qt.MouseButton.LeftButton)
        wait_review()
        self.assertEqual(panel.detail['order'], 2)
        favorite = panel.question_list.itemWidget(panel.question_list.currentItem()).findChild(QPushButton)
        QTest.mouseClick(favorite, Qt.MouseButton.LeftButton)
        wait_review()
        self.assertTrue(panel.detail['favorite'])
        panel.favorites_only.setChecked(True)
        wait_review()
        self.assertEqual(panel.question_list.count(), 1)
        self.assertEqual(panel.detail['order'], 2)
        self.assertFalse(panel.next_button.isEnabled())
        stored = json.loads((self.window.export_dir/'review.json').read_text(encoding='utf-8'))
        self.assertTrue(stored['questions'][rows[1]['key']]['favorite'])
        panel.favorites_only.setChecked(False)
        wait_review()
        QTest.mouseClick(panel.previous_button, Qt.MouseButton.LeftButton)
        wait_review()
        self.assertEqual(panel.detail['order'], 1)
        self.window.tabs.setCurrentWidget(self.window.correction_panel)
        deadline = time.monotonic()+20
        while (correction.job or correction.queue or not correction.detail) and time.monotonic() < deadline:
            QTest.qWait(40)
        self.assertIsNotNone(correction.detail, self.window.status_label.text())
        self.assertEqual(correction.detail['order'], 2)
        save_correction(self.window.export_dir, 1, '官方答案为 B',
                        '**结论**\n\n选 B\n\n**推导过程**\n修正后的说明。')
        self.window.tabs.setCurrentWidget(self.window.review_panel)
        wait_review()
        self.assertEqual(panel.detail['order'], 1)
        self.assertEqual([section.get('label') for section in panel.question_view.sections[1:]],
                         ['修正答案'])
        self.assertIn('修正后的说明', panel.question_view.sections[1]['text'])
        self.assertNotIn('测试公式', panel.question_view.sections[1]['text'])
        QTest.mouseClick(panel.copy_button, Qt.MouseButton.LeftButton)
        wait_review()
        self.assertIn('修正后的说明', APP.clipboard().text())
        self.assertNotIn('测试公式', APP.clipboard().text())


if __name__ == '__main__':
    unittest.main()
