"""Qt review tab; question operations run through the CLI."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import QProcess, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListView, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

from markdown_preview import MarkdownPreview


class ElidedLabel(QLabel):
    def __init__(self, full_text):
        super().__init__(full_text)
        self.full_text = full_text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setToolTip(full_text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setText(self.fontMetrics().elidedText(
            self.full_text, Qt.TextElideMode.ElideRight, max(0, self.contentsRect().width())))


class ReviewPanel(QWidget):
    statusChanged = Signal(str, bool)
    apiBusyChanged = Signal(bool)
    reasoningRequested = Signal()

    def __init__(self, command_factory, parent=None, api_context=None, mode='review'):
        super().__init__(parent)
        if mode not in {'review', 'correction'}:
            raise ValueError('未知的复习面板模式')
        self.mode = mode
        self.command_factory = command_factory
        self.api_context = api_context
        self.export_dir = None
        self.active = False
        self.generation = 0
        self.rows = []
        self.detail = None
        self.queue = []
        self.job = None
        self.raw = b''
        self.correction_drafts = {}
        self.correction_dirty = set()
        self.correction_loading = False
        self.api_job = None
        self.last_api_request = None
        self.api_raw = b''
        self.api_pending = b''
        self.correction_timer = QTimer(self)
        self.correction_timer.setSingleShot(True)
        self.correction_timer.setInterval(700)
        self.correction_timer.timeout.connect(self._save_dirty)
        self.api_process = QProcess(self)
        self.api_process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.api_process.readyReadStandardOutput.connect(self._read_api)
        self.api_process.finished.connect(self._api_finished)
        self.api_process.errorOccurred.connect(self._api_error)
        self.question_view = None
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left.setMinimumWidth(200)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 10, 0)
        self.count_label = QLabel('题目 · 0 题', objectName='section')
        left_layout.addWidget(self.count_label)
        self.favorites_only = QCheckBox('只看收藏')
        self.favorites_only.toggled.connect(self._populate)
        left_layout.addWidget(self.favorites_only)
        self.favorites_only.setVisible(self.mode == 'review')
        self.question_list = QListWidget()
        self.question_list.setObjectName('questions')
        self.question_list.setSpacing(4)
        self.question_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.question_list.currentItemChanged.connect(self._select)
        left_layout.addWidget(self.question_list, 1)
        splitter.addWidget(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(10)
        self.question_title = QLabel('请选择一道题', objectName='section')
        right_layout.addWidget(self.question_title)
        self.question_container = QVBoxLayout()
        self.question_placeholder = QLabel(
            '先在“资料制作”中转换文件，再选择题目开始'+('纠错。' if self.mode == 'correction' else '复习。'),
            objectName='muted')
        self.question_placeholder.setWordWrap(True)
        self.question_container.addWidget(self.question_placeholder)
        right_layout.addLayout(self.question_container, 1)
        correction = QFrame()
        correction.setObjectName('settings')
        correction_layout = QVBoxLayout(correction)
        correction_layout.setContentsMargins(12, 10, 12, 10)
        correction_layout.setSpacing(7)
        correction_layout.addWidget(QLabel('答案纠错与补充', objectName='section'))
        self.correction_note = QLineEdit()
        self.correction_note.setPlaceholderText('输入官方答案、官方解析及需要补充的纠错信息')
        self.correction_note.textChanged.connect(self._correction_changed)
        correction_layout.addWidget(self.correction_note)
        correction_splitter = QSplitter(Qt.Orientation.Horizontal)
        manual = QWidget()
        manual_layout = QVBoxLayout(manual)
        manual_layout.setContentsMargins(0, 0, 8, 0)
        copy_actions = QHBoxLayout()
        self.copy_image_button = QPushButton('复制题目图片')
        self.copy_image_button.clicked.connect(lambda: self._correction_action('image'))
        copy_actions.addWidget(self.copy_image_button)
        self.copy_correction_prompt_button = QPushButton('复制提示词')
        self.copy_correction_prompt_button.clicked.connect(lambda: self._correction_action('prompt'))
        copy_actions.addWidget(self.copy_correction_prompt_button)
        manual_layout.addLayout(copy_actions)
        self.correction_answer = QPlainTextEdit()
        self.correction_answer.setPlaceholderText('粘贴修正答案的 Markdown；按题目分别保存。')
        self.correction_answer.setMinimumHeight(125)
        self.correction_answer.textChanged.connect(self._correction_changed)
        manual_layout.addWidget(self.correction_answer, 1)
        correction_splitter.addWidget(manual)
        api = QWidget()
        api_layout = QVBoxLayout(api)
        api_layout.setContentsMargins(8, 0, 0, 0)
        api_layout.setSpacing(5)
        api_layout.addWidget(QLabel('使用 API', objectName='section'))
        self.review_api_combo = QComboBox()
        self.review_api_combo.setView(QListView())
        self.review_api_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.review_api_combo.currentIndexChanged.connect(self._review_profile_changed)
        api_layout.addWidget(self.review_api_combo)
        self.review_reasoning_combo = QComboBox()
        self.review_reasoning_combo.setView(QListView())
        self.review_reasoning_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        api_layout.addWidget(QLabel('思考强度'))
        api_layout.addWidget(self.review_reasoning_combo)
        self.retry_review_reasoning_button = QPushButton('重试获取思考强度')
        self.retry_review_reasoning_button.clicked.connect(self.reasoningRequested.emit)
        self.retry_review_reasoning_button.setToolTip('目录缺少档位时发送一次最小测试请求；'
                                                     '不发送题目，可能产生少量 API 费用。')
        api_layout.addWidget(self.retry_review_reasoning_button)
        self.call_correction_api_button = QPushButton('直接调用 API')
        self.call_correction_api_button.clicked.connect(self.start_correction_api)
        api_layout.addWidget(self.call_correction_api_button)
        self.retry_correction_api_button = QPushButton('重试请求')
        self.retry_correction_api_button.clicked.connect(self.retry_correction_api)
        api_layout.addWidget(self.retry_correction_api_button)
        correction_splitter.addWidget(api)
        correction_splitter.setSizes([480, 240])
        correction_splitter.setStretchFactor(0, 1)
        correction_layout.addWidget(correction_splitter)
        right_layout.addWidget(correction)
        correction.setVisible(self.mode == 'correction')
        navigation = QHBoxLayout()
        self.previous_button = QPushButton('上一题')
        self.previous_button.clicked.connect(lambda: self._move(-1))
        self.next_button = QPushButton('下一题')
        self.next_button.clicked.connect(lambda: self._move(1))
        navigation.addWidget(self.previous_button)
        navigation.addWidget(self.next_button)
        navigation.addStretch()
        self.copy_button = QPushButton('复制到 Cherry Studio')
        self.copy_button.setObjectName('primary')
        self.copy_button.setToolTip('复制当前题目和已导入解答的 Markdown；题图可在预览中右键复制后另行粘贴。')
        self.copy_button.clicked.connect(self.copy_markdown)
        navigation.addWidget(self.copy_button)
        self.copy_button.setVisible(self.mode == 'review')
        right_layout.addLayout(navigation)
        splitter.addWidget(right)
        splitter.setSizes([250, 790])
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)
        self._buttons()

    def activate(self):
        if not self.active:
            self.active = True
            self.question_placeholder.hide()
            self.question_view = MarkdownPreview(self)
            self.question_container.addWidget(self.question_view)
            if self.export_dir:
                self._enqueue('list')
        if self.mode == 'correction':
            self.sync_api_options()

    def set_export(self, directory, refresh=False):
        directory = Path(directory).resolve() if directory else None
        if directory == self.export_dir and not refresh:
            return
        self._save_dirty()
        pending_saves = [job for job in self.queue if job['kind'] == 'save']
        self.generation += 1
        self.export_dir = directory
        if not refresh:
            self.preferred_key = None
            self.correction_drafts.clear()
            self.correction_dirty.clear()
        self.queue = pending_saves
        self.rows, self.detail = [], None
        self.question_list.blockSignals(True)
        self.question_list.clear()
        self.question_list.blockSignals(False)
        self.count_label.setText('题目 · 0 题')
        self.question_title.setText('请选择一道题')
        self.correction_loading = True
        self.correction_note.clear()
        self.correction_answer.clear()
        self.correction_loading = False
        self._buttons()
        if self.active:
            self.question_view.set_sections([{'text': '请选择一道题。' if directory else '请先在“资料制作”中转换文件。'}])
            if directory:
                self._enqueue('list')

    def _enqueue(self, kind, order=None, value=None, payload=None):
        if not self.export_dir:
            return
        job = {'kind': kind, 'order': order, 'value': value,
               'payload': payload, 'generation': self.generation, 'source': self.export_dir}
        if kind == 'question':
            self.queue = [j for j in self.queue if j['kind'] != 'question']
        if kind == 'save':
            self.queue = [j for j in self.queue if not (j['kind'] == 'save' and j['order'] == order)]
        self.queue.append(job)
        self._next_job()

    def _next_job(self):
        if self.job or not self.queue:
            return
        self.job = self.queue.pop(0)
        self.raw = b''
        correction = self.job['kind'] in {'save', 'prompt', 'image'}
        args = [self.job['kind'], self.job['source']]
        if self.job['order'] is not None:
            args += ['--order', self.job['order']]
        if self.job['value'] is not None:
            args += ['--value', 'true' if self.job['value'] else 'false']
        if self.job['payload'] is not None:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', encoding='utf-8',
                                             delete=False) as temporary:
                json.dump(self.job['payload'], temporary, ensure_ascii=False)
            self.job['input'] = Path(temporary.name)
            args += ['--input', self.job['input']]
        program, arguments = self.command_factory(
            'review_correction.py' if correction else 'review_homework.py', args)
        self.process.start(program, arguments)
        self._buttons()

    def _read(self):
        self.raw += bytes(self.process.readAllStandardOutput())

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.raw = json.dumps({'message': self.process.errorString()}).encode('utf-8')
            self._finished(-1, QProcess.ExitStatus.CrashExit)

    def _finished(self, code, status):
        if self.job is None:
            return
        self._read()
        job, self.job = self.job, None
        if job.get('input'):
            job['input'].unlink(missing_ok=True)
        if job['generation'] == self.generation:
            try:
                result = json.loads(self.raw.decode('utf-8'))
                if code or status != QProcess.ExitStatus.NormalExit:
                    raise ValueError(result.get('message', 'CLI 执行失败'))
                if job['kind'] == 'list':
                    self.rows = result['questions']
                    self._populate()
                elif job['kind'] == 'copy':
                    QApplication.clipboard().setText(result['text'])
                    message = f'已复制第 {result["order"]} 题的 Markdown，可粘贴到 Cherry Studio。'
                    if result['has_images']:
                        message += '题图请在预览中右键复制后另行粘贴。'
                    self.statusChanged.emit(message, False)
                elif job['kind'] == 'favorite':
                    for row in self.rows:
                        if row['key'] == result['key']:
                            row['favorite'] = result['favorite']
                    self._populate()
                    self.statusChanged.emit('已收藏这道题。' if result['favorite'] else '已取消收藏。', False)
                elif job['kind'] == 'image':
                    image = QImage(result['image'])
                    if image.isNull():
                        raise ValueError('单题图片无法读取')
                    QApplication.clipboard().setImage(image)
                    self.statusChanged.emit(f'已复制第 {job["order"]} 题的图片。', False)
                elif job['kind'] == 'prompt':
                    QApplication.clipboard().setText(result['prompt'])
                    self.statusChanged.emit(f'已复制第 {job["order"]} 题的纠错提示词。', False)
                elif job['kind'] == 'save':
                    for row in self.rows:
                        if row['key'] == result['key']:
                            row['conclusion'] = result['conclusion']
                    self._update_summary(result['key'])
                    if self.detail and self.detail['key'] == result['key']:
                        self.detail.update(correction_note=result['correction_note'],
                                           corrected_answer=result['corrected_answer'])
                        self._render_detail()
                else:
                    item = self.question_list.currentItem()
                    if item and item.data(Qt.ItemDataRole.UserRole)['key'] == result['key']:
                        self._show_detail(result)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.statusChanged.emit(f'读取复习资料失败：{exc}', True)
        self._next_job()
        self._buttons()

    def _populate(self, *args):
        self._save_dirty()
        selected = self.detail['key'] if self.detail else self.preferred_key
        if self.question_list.currentItem():
            selected = self.question_list.currentItem().data(Qt.ItemDataRole.UserRole)['key']
        self.question_list.blockSignals(True)
        self.question_list.clear()
        chosen = 0
        for row in self.rows:
            if self.mode == 'review' and self.favorites_only.isChecked() and not row['favorite']:
                continue
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, row)
            item.setSizeHint(QSize(160, 49))
            conclusion = row.get('conclusion', '')
            item.setToolTip(f'{row["title"]} · 原题号 {row["number"]} · {row["type"]}'
                            +(f'\n结论：{conclusion}' if conclusion else ''))
            self.question_list.addItem(item)
            card = QWidget()
            horizontal = QHBoxLayout(card)
            horizontal.setContentsMargins(10, 4, 6, 4)
            label = ElidedLabel(f'第 {row["order"]} 题'+(f' · {conclusion}' if conclusion else ''))
            label.setObjectName('reviewQuestionSummary')
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            horizontal.addWidget(label, 1)
            if self.mode == 'review':
                favorite = QPushButton('★' if row['favorite'] else '☆')
                favorite.setFixedSize(30, 30)
                favorite.setStyleSheet('padding: 0; min-height: 24px; color: '+('#946200;' if row['favorite'] else '#51677c;'))
                favorite.setToolTip('取消收藏' if row['favorite'] else '收藏此题')
                favorite.clicked.connect(lambda checked=False, r=row: self._enqueue('favorite', r['order'], not r['favorite']))
                horizontal.addWidget(favorite)
            self.question_list.setItemWidget(item, card)
            if row['key'] == selected:
                chosen = self.question_list.count()-1
        self.question_list.blockSignals(False)
        self.count_label.setText(f'题目 · {len(self.rows)} 题')
        if self.question_list.count():
            self.question_list.setCurrentRow(chosen)
        else:
            self._select(None)

    def _select(self, item, previous=None):
        if previous is not None:
            self._save_key(previous.data(Qt.ItemDataRole.UserRole)['key'])
        self.detail = None
        self.preferred_key = None
        if item:
            row = item.data(Qt.ItemDataRole.UserRole)
            self.preferred_key = row['key']
            self.question_title.setText(f'第 {row["order"]} 题 · {row["type"]}')
            if self.mode == 'correction':
                self._load_correction(row['key'])
            self._enqueue('question', row['order'])
        else:
            self.question_title.setText('没有可显示的题目')
            self._load_correction(None)
        if self.active:
            self.question_view.set_sections([{'text': '正在加载题目…' if item else '没有可显示的题目。'}])
        self._buttons()

    def _show_detail(self, detail):
        self.detail = detail
        if self.mode == 'correction' and detail['key'] not in self.correction_drafts:
            self.correction_drafts[detail['key']] = {
                'note': detail.get('correction_note', ''),
                'answer': detail.get('corrected_answer', '')}
        if self.mode == 'correction':
            self._load_correction(detail['key'])
        self._render_detail()
        self._buttons()

    def _render_detail(self):
        detail = self.detail
        if detail is None:
            return
        sections = [{'text': detail['text'], 'question': True}]
        if self.mode == 'correction':
            if detail['answer']:
                sections.append({'label': '已导入解答', 'text': detail['answer']})
            draft = self.correction_drafts.get(detail['key'], {})
            if draft.get('note'):
                sections.append({'label': '官方答案与解析', 'text': draft['note']})
            if draft.get('answer'):
                sections.append({'label': '综合修正解析', 'text': draft['answer']})
        else:
            corrected = detail.get('corrected_answer', '')
            answer = corrected if corrected.strip() else detail['answer']
            if answer:
                sections.append({'label': '修正答案' if corrected.strip() else '已导入解答',
                                 'text': answer})
        self.question_view.set_sections(sections, self.export_dir)

    def _current_key(self):
        item = self.question_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole)['key'] if item else None

    def _load_correction(self, key):
        values = self.correction_drafts.get(key, {})
        self.correction_loading = True
        self.correction_note.setText(values.get('note', ''))
        self.correction_answer.setPlainText(values.get('answer', ''))
        self.correction_loading = False
        self._buttons()

    def _correction_changed(self):
        if self.correction_loading:
            return
        key = self._current_key()
        if key is None:
            return
        self.correction_drafts[key] = {'note': self.correction_note.text(),
                                       'answer': self.correction_answer.toPlainText()}
        self.correction_dirty.add(key)
        self.correction_timer.start()
        self._buttons()

    def _save_current(self):
        self._save_key(self._current_key())

    def _save_dirty(self):
        for key in tuple(self.correction_dirty):
            self._save_key(key)

    def _save_key(self, key):
        if key not in self.correction_dirty or not self.export_dir:
            return
        self.correction_timer.stop()
        self.correction_dirty.remove(key)
        row = next((r for r in self.rows if r['key'] == key), None)
        if row:
            self._enqueue('save', row['order'], payload=self.correction_drafts[key].copy())

    def _update_summary(self, key):
        for index in range(self.question_list.count()):
            item = self.question_list.item(index)
            row = item.data(Qt.ItemDataRole.UserRole)
            if row['key'] == key:
                card = self.question_list.itemWidget(item)
                label = card.findChild(ElidedLabel, 'reviewQuestionSummary')
                conclusion = row.get('conclusion', '')
                label.full_text = f'第 {row["order"]} 题'+(f' · {conclusion}' if conclusion else '')
                label.setToolTip(label.full_text)
                label.setText(label.fontMetrics().elidedText(
                    label.full_text, Qt.TextElideMode.ElideRight, label.width()))
                return

    def _correction_action(self, kind):
        item = self.question_list.currentItem()
        if item is None:
            return
        row = item.data(Qt.ItemDataRole.UserRole)
        values = self.correction_drafts.get(row['key'], {'note': '', 'answer': ''}).copy()
        if kind == 'prompt' and not values['note'].strip():
            self.statusChanged.emit('请先填写平台的正确答案或纠错信息。', True)
            return
        self._save_current()
        self.statusChanged.emit('正在准备第 %s 题的%s…' % (
            row['order'], '题目图片' if kind == 'image' else '纠错提示词'), False)
        self._enqueue(kind, row['order'], payload=values if kind == 'prompt' else None)

    def sync_api_options(self):
        if self.api_context is None:
            return
        context = self.api_context()
        selected = context['profile']
        effort = self.review_reasoning_combo.currentData() if self.review_reasoning_combo.count() else context['effort']
        self.review_api_combo.blockSignals(True)
        self.review_api_combo.clear()
        for name, profile_id in context['profiles']:
            self.review_api_combo.addItem(name, profile_id)
        self.review_api_combo.setCurrentIndex(max(0, self.review_api_combo.findData(selected)))
        self.review_api_combo.blockSignals(False)
        self.review_reasoning_combo.clear()
        for label, value in context['efforts']:
            self.review_reasoning_combo.addItem(label, value)
        self.review_reasoning_combo.setCurrentIndex(max(0, self.review_reasoning_combo.findData(effort)))
        self._buttons()

    def _review_profile_changed(self):
        if self.parent() is not None and hasattr(self.parent(), 'api_combo'):
            combo = self.parent().api_combo
            index = combo.findData(self.review_api_combo.currentData())
            if index >= 0 and index != combo.currentIndex():
                combo.setCurrentIndex(index)
        self._buttons()

    def start_correction_api(self):
        if self.api_job:
            self.api_job['cancelled'] = True
            self.api_process.kill()
            return
        if self.api_context and self.api_context().get('batch_running'):
            self.statusChanged.emit('请先停止批量调用，再开始纠错 API。', True)
            return
        item = self.question_list.currentItem()
        if item is None or self.export_dir is None:
            return
        row = item.data(Qt.ItemDataRole.UserRole)
        values = self.correction_drafts.get(row['key'], {'note': '', 'answer': ''}).copy()
        if not values['note'].strip():
            self.statusChanged.emit('请先填写平台的正确答案或纠错信息。', True)
            return
        request = {'source': self.export_dir, 'order': row['order'], 'key': row['key'],
                   'values': values, 'profile': self.review_api_combo.currentData(),
                   'effort': self.review_reasoning_combo.currentData(),
                   'parameter': self.api_context()['parameter'],
                   'config': self.api_context()['config']}
        self._launch_correction_api(request)

    def retry_correction_api(self):
        if self.api_context and self.api_context().get('batch_running'):
            return
        if (self.api_job is None and self.last_api_request and
                self.last_api_request['source'] == self.export_dir and
                self.last_api_request['key'] in self.correction_drafts):
            self._launch_correction_api(self.last_api_request.copy())

    def _launch_correction_api(self, request):
        self._save_current()
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', encoding='utf-8',
                                         delete=False) as temporary:
            json.dump(request['values'], temporary, ensure_ascii=False)
        request = request.copy()
        request['input'] = Path(temporary.name)
        request['previous'] = self.correction_drafts[request['key']]['answer']
        request['text'] = ''
        request['result'] = None
        request['error'] = ''
        request['cancelled'] = False
        self.api_job = request
        self.last_api_request = {k: v for k, v in request.items()
                                 if k not in {'input', 'previous', 'text', 'result', 'error', 'cancelled'}}
        args = ['api', request['source'], '--order', request['order'], '--input', request['input'],
                '--config', request['config'], '--profile', request['profile']]
        if request['effort'] is not None:
            args += ['--reasoning-effort', request['effort'],
                     '--reasoning-parameter', request['parameter']]
        program, arguments = self.command_factory('review_correction.py', args)
        self.api_raw = self.api_pending = b''
        self.api_process.start(program, arguments)
        self.apiBusyChanged.emit(True)
        self.statusChanged.emit(f'正在纠正第 {request["order"]} 题…', False)
        self._buttons()

    def _read_api(self):
        self.api_pending += bytes(self.api_process.readAllStandardOutput())
        while b'\n' in self.api_pending:
            line, self.api_pending = self.api_pending.split(b'\n', 1)
            if line.startswith(b'@@API '):
                try:
                    self._api_event(json.loads(line[6:].decode('utf-8')))
                except (ValueError, UnicodeError):
                    pass
            else:
                self.api_raw += line+b'\n'

    def _api_error(self, error):
        if error == QProcess.ProcessError.FailedToStart and self.api_job:
            self.api_job['error'] = self.api_process.errorString()
            self._api_finished(-1, QProcess.ExitStatus.CrashExit)

    def _api_event(self, event):
        job = self.api_job
        if job is None:
            return
        kind = event.get('type')
        if kind == 'delta':
            job['text'] += event.get('text', '')
            self.correction_drafts[job['key']]['answer'] = job['text']
            if self._current_key() == job['key']:
                self._load_correction(job['key'])
            self.statusChanged.emit(f'第 {job["order"]} 题正在接收修正答案 · {len(job["text"])} 字符', False)
        elif kind == 'result':
            job['result'] = event
        elif kind == 'error':
            job['error'] = event.get('message', '')
        elif kind == 'status':
            self.statusChanged.emit(event.get('message', ''), False)

    def _api_finished(self, code, status):
        if self.api_job is None:
            return
        self._read_api()
        job, self.api_job = self.api_job, None
        self.apiBusyChanged.emit(False)
        job['input'].unlink(missing_ok=True)
        result = job['result']
        valid = (not code and status == QProcess.ExitStatus.NormalExit and result
                 and result.get('valid') is True and not job['cancelled'])
        self.correction_drafts[job['key']]['answer'] = result['answer'] if valid else job['previous']
        if self._current_key() == job['key']:
            self._load_correction(job['key'])
        if valid:
            self.correction_dirty.add(job['key'])
            self._enqueue('save', job['order'], payload=self.correction_drafts[job['key']].copy())
            self.correction_dirty.discard(job['key'])
            self.statusChanged.emit(f'第 {job["order"]} 题修正答案已返回，正在保存。', False)
        elif job['cancelled']:
            self.statusChanged.emit('纠错 API 请求已停止，保留原修正答案。', False)
        else:
            reason = job['error'] or (result or {}).get('message', '请求没有返回完整答案')
            self.statusChanged.emit(f'纠错 API 未保存：{reason}', True)
        self._buttons()

    def _move(self, offset):
        index = self.question_list.currentRow()+offset
        if 0 <= index < self.question_list.count():
            self.question_list.setCurrentRow(index)

    def _buttons(self):
        current = self.question_list.currentRow()
        self.previous_button.setEnabled(current > 0)
        self.next_button.setEnabled(0 <= current < self.question_list.count()-1)
        self.copy_button.setEnabled(self.detail is not None and self.job is None)
        key = self._current_key()
        occupied = self.api_job is not None and self.api_job['key'] == key
        batch_running = bool(self.api_context and self.review_api_combo.count()
                             and self.api_context().get('batch_running'))
        self.correction_note.setEnabled(key is not None)
        self.correction_note.setReadOnly(occupied)
        self.correction_answer.setEnabled(key is not None)
        self.correction_answer.setReadOnly(occupied)
        self.copy_image_button.setEnabled(key is not None)
        self.copy_correction_prompt_button.setEnabled(key is not None)
        self.review_api_combo.setEnabled(not batch_running and self.api_job is None and bool(self.review_api_combo.count()))
        self.review_reasoning_combo.setEnabled(not batch_running and self.api_job is None and bool(self.review_reasoning_combo.count()))
        self.retry_review_reasoning_button.setEnabled(not batch_running and self.api_job is None and
            self.review_api_combo.currentData() is not None)
        self.call_correction_api_button.setText('停止请求' if self.api_job else '直接调用 API')
        self.call_correction_api_button.setEnabled(not batch_running and bool(self.api_job or
            (key and self.review_api_combo.currentData() and self.correction_note.text().strip())))
        self.retry_correction_api_button.setEnabled(bool(not batch_running and not self.api_job and self.last_api_request and
            self.last_api_request['source'] == self.export_dir and
            self.last_api_request['key'] in self.correction_drafts))

    def copy_markdown(self):
        if self.detail and not self.job:
            self._save_current()
            self._enqueue('copy', self.detail['order'])

    def shutdown(self):
        self._save_dirty()
        saves = [job for job in ([self.job] if self.job else [])+self.queue if job['kind'] == 'save']
        if self.api_process.state() != QProcess.ProcessState.NotRunning:
            self.api_process.finished.disconnect(self._api_finished)
            self.api_process.kill()
            self.api_process.waitForFinished(1000)
        if self.api_job and self.api_job.get('input'):
            self.api_job['input'].unlink(missing_ok=True)
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.finished.disconnect(self._finished)
            self.process.kill()
            self.process.waitForFinished(1000)
        for job in saves:
            input_path = job.get('input')
            if input_path is None:
                with tempfile.NamedTemporaryFile(mode='w', suffix='.json', encoding='utf-8',
                                                 delete=False) as temporary:
                    json.dump(job['payload'], temporary, ensure_ascii=False)
                input_path = Path(temporary.name)
            try:
                program, args = self.command_factory('review_correction.py',
                    ['save', job['source'], '--order', job['order'], '--input', input_path])
                subprocess.run([program, *args], capture_output=True, timeout=10, check=False)
            finally:
                input_path.unlink(missing_ok=True)
        if self.job and self.job.get('input') and self.job['kind'] != 'save':
            self.job['input'].unlink(missing_ok=True)
        if self.question_view:
            self.question_view.page().deleteLater()
