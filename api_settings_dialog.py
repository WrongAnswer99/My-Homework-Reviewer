"""Qt editor for local API profiles; edits are committed only by Save."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListView, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from api_config import load_profiles, new_profile, normalize_profile, save_profiles


class APISettingsDialog(QDialog):
    def __init__(self, config_path, parent=None):
        super().__init__(parent)
        self.config_path = config_path
        self.current_id = None
        self.models_process = None
        self.setWindowTitle('API 配置')
        self.resize(820, 740)
        self.setMinimumSize(720, 600)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.addWidget(QLabel('API 配置', objectName='heading'))
        outer.addWidget(QLabel('保存到本地明文配置；每个条目对应一个模型。', objectName='muted'))
        columns = QHBoxLayout()
        left = QVBoxLayout()
        self.profile_list = QListWidget()
        self.profile_list.setObjectName('apiProfiles')
        self.profile_list.setMinimumWidth(190)
        self.profile_list.setMaximumWidth(250)
        left.addWidget(self.profile_list, 1)
        buttons = QHBoxLayout()
        self.add_button = QPushButton('添加')
        self.delete_button = QPushButton('删除')
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.delete_button)
        left.addLayout(buttons)
        columns.addLayout(left)
        self.editor = QWidget()
        self.editor.setObjectName('apiEditor')
        form = QFormLayout(self.editor)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setVerticalSpacing(12)
        self.name_edit = QLineEdit()
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText('填写服务商提供的模型 ID')
        self.model_combo = QComboBox()
        self.model_combo.setView(QListView())
        self.model_combo.setMinimumWidth(0)
        self.model_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.model_combo.addItem('使用自定义模型id', None)
        self.fetch_models_button = QPushButton('获取模型列表')
        self.fetch_models_button.setToolTip('用当前 Base URL 和 API Key 只读查询模型目录，不调用模型生成内容。')
        self.fetch_models_button.clicked.connect(self.fetch_models)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model_combo, 1)
        model_row.addWidget(self.fetch_models_button)
        self.custom_model_label = QLabel('自定义模型 ID')
        self.model_combo.currentIndexChanged.connect(self._model_selection_changed)
        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText('https://服务地址/v1')
        self.base_url_edit.setToolTip('填写兼容接口的 Base URL，包含 /v1 或服务商要求的前缀；也支持完整 /chat/completions 地址。')
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        key_row = QHBoxLayout()
        key_row.addWidget(self.api_key_edit, 1)
        self.show_key = QCheckBox('显示')
        self.show_key.toggled.connect(lambda checked: self.api_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password))
        key_row.addWidget(self.show_key)
        self.temperature_spin = QDoubleSpinBox()
        self.temperature_spin.setRange(0, 2)
        self.temperature_spin.setSingleStep(0.1)
        self.top_p_spin = QDoubleSpinBox()
        self.top_p_spin.setRange(0.01, 1)
        self.top_p_spin.setSingleStep(0.05)
        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(0, 1000000)
        self.max_tokens_spin.setSpecialValueText('不发送此参数')
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 3600)
        self.timeout_spin.setSuffix(' 秒')
        self.stream_check = QCheckBox('流式显示解答')
        self.image_check = QCheckBox('发送当前卡片题图（需要模型支持图片）')
        self.system_edit = QPlainTextEdit()
        self.system_edit.setMaximumHeight(90)
        self.system_edit.setPlaceholderText('可选：额外的系统提示词')
        self.extra_edit = QPlainTextEdit()
        self.extra_edit.setMaximumHeight(100)
        self.extra_edit.setPlaceholderText('可选 JSON 对象，例如 {"enable_thinking": false}')
        for label, field in [('API 展示名称', self.name_edit),
                              ('Base URL', self.base_url_edit), ('API Key', key_row),
                              ('模型 ID', model_row), (self.custom_model_label, self.model_edit),
                              ('Temperature', self.temperature_spin), ('Top P', self.top_p_spin),
                              ('最大输出 Token', self.max_tokens_spin), ('连接 / 读取超时', self.timeout_spin),
                              ('返回方式', self.stream_check), ('题图', self.image_check),
                              ('系统提示词', self.system_edit), ('额外请求参数', self.extra_edit)]:
            form.addRow(label, field)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.editor)
        columns.addWidget(scroll, 1)
        outer.addLayout(columns, 1)
        self.message = QLabel('接口格式：兼容 Chat Completions；API Key 留空可用于无需鉴权的本地服务。')
        self.message.setObjectName('muted')
        self.message.setWordWrap(True)
        outer.addWidget(self.message)
        footer = QHBoxLayout()
        footer.addStretch()
        self.cancel_button = QPushButton('取消')
        self.cancel_button.clicked.connect(self.reject)
        self.save_button = QPushButton('保存')
        self.save_button.setObjectName('primary')
        self.save_button.clicked.connect(self.save)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.save_button)
        outer.addLayout(footer)
        self.add_button.clicked.connect(self.add_profile)
        self.delete_button.clicked.connect(self.delete_profile)
        self.profile_list.currentItemChanged.connect(self._select)
        self.name_edit.textChanged.connect(self._rename)
        self.base_url_edit.textChanged.connect(self._model_endpoint_changed)
        self.api_key_edit.textChanged.connect(self._model_endpoint_changed)
        self.load_failed = False
        try:
            profiles = load_profiles(config_path)
        except (OSError, ValueError, TypeError) as exc:
            profiles = []
            self.load_failed = True
            self.message.setText(f'配置读取失败：{exc}；请修复配置文件后重新打开，原文件不会被覆盖。')
        for profile in profiles:
            self._append(profile)
        self.save_button.setEnabled(not self.load_failed)
        self.add_button.setEnabled(not self.load_failed)
        if self.profile_list.count():
            self.profile_list.setCurrentRow(0)
        else:
            self._select(None)

    def _append(self, profile):
        item = QListWidgetItem(profile['name'])
        item.setData(Qt.ItemDataRole.UserRole, profile)
        self.profile_list.addItem(item)
        return item

    def _draft(self):
        return {'id': self.current_id, 'name': self.name_edit.text(),
                'model': self._selected_model(), 'base_url': self.base_url_edit.text(),
                '_model_options': [self.model_combo.itemData(i) for i in range(self.model_combo.count()-1)],
                '_model_selection': self.model_combo.currentData(), '_custom_model': self.model_edit.text(),
                'api_key': self.api_key_edit.text(), 'temperature': self.temperature_spin.value(),
                'top_p': self.top_p_spin.value(), 'max_tokens': self.max_tokens_spin.value(),
                'timeout': self.timeout_spin.value(), 'stream': self.stream_check.isChecked(),
                'send_image': self.image_check.isChecked(), 'system_prompt': self.system_edit.toPlainText(),
                'extra_body': self.extra_edit.toPlainText()}

    def _select(self, item, previous=None):
        if previous and self.current_id:
            previous.setData(Qt.ItemDataRole.UserRole, self._draft())
        self.current_id = None
        self._cancel_models_request()
        self.editor.setEnabled(item is not None)
        self.delete_button.setEnabled(item is not None)
        if not item:
            for field in (self.name_edit, self.model_edit, self.base_url_edit, self.api_key_edit):
                field.clear()
            self._set_model_options([], None)
            self.system_edit.clear()
            self.extra_edit.clear()
            return
        profile = item.data(Qt.ItemDataRole.UserRole)
        self.name_edit.setText(profile['name'])
        self.model_edit.setText(profile.get('_custom_model', profile['model']))
        self.base_url_edit.setText(profile['base_url'])
        self.api_key_edit.setText(profile['api_key'])
        self._set_model_options(profile.get('_model_options', []), profile.get('_model_selection'))
        self.show_key.setChecked(False)
        self.temperature_spin.setValue(profile['temperature'])
        self.top_p_spin.setValue(profile['top_p'])
        self.max_tokens_spin.setValue(profile['max_tokens'])
        self.timeout_spin.setValue(profile['timeout'])
        self.stream_check.setChecked(profile['stream'])
        self.image_check.setChecked(profile['send_image'])
        self.system_edit.setPlainText(profile['system_prompt'])
        extra = profile['extra_body']
        self.extra_edit.setPlainText(extra if isinstance(extra, str) else json.dumps(extra, ensure_ascii=False, indent=2))
        self.current_id = profile['id']

    def _selected_model(self):
        selected = self.model_combo.currentData()
        return self.model_edit.text() if selected is None else selected

    def _model_selection_changed(self, *args):
        custom = self.model_combo.currentData() is None
        self.model_edit.setVisible(custom)
        self.custom_model_label.setVisible(custom)

    def _set_model_options(self, models, selected):
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for model in models:
            self.model_combo.addItem(model, model)
        self.model_combo.addItem('使用自定义模型id', None)
        index = self.model_combo.findData(selected) if selected is not None else -1
        self.model_combo.setCurrentIndex(index if index >= 0 else self.model_combo.count()-1)
        self.model_combo.blockSignals(False)
        self._model_selection_changed()

    def _cancel_models_request(self):
        process = self.models_process
        self.models_process = None
        if process is not None:
            process.kill()
        self.fetch_models_button.setEnabled(True)
        self.fetch_models_button.setText('获取模型列表')

    def _model_endpoint_changed(self, *args):
        if self.current_id is None:
            return
        model = self._selected_model()
        self._cancel_models_request()
        self.model_edit.setText(model)
        self._set_model_options([], None)

    def fetch_models(self):
        if self.current_id is None or self.models_process is not None:
            return
        # Validate only connection fields; a model ID and valid extra JSON are
        # deliberately not required to query the catalog.
        profile = new_profile()
        profile.update(base_url=self.base_url_edit.text(), api_key=self.api_key_edit.text(),
                       timeout=self.timeout_spin.value())
        try:
            profile = normalize_profile(profile)
            if not profile['base_url']:
                raise ValueError('请先填写 Base URL')
        except ValueError as exc:
            self.message.setText(str(exc))
            return
        process = QProcess(self)
        self.models_process = process
        self.fetch_models_button.setEnabled(False)
        self.fetch_models_button.setText('获取中…')
        self.message.setText('正在获取模型列表…')
        process.finished.connect(lambda code, status, p=process: self._models_finished(p, code, status))
        process.errorOccurred.connect(lambda error, p=process: self._models_error(p, error))
        python = Path(sys.executable)
        if python.name.lower() == 'pythonw.exe':
            python = python.with_name('python.exe')
        process.start(str(python), ['-B', '-X', 'utf8', str(Path(__file__).with_name('api_models.py'))])
        process.write(json.dumps(profile, ensure_ascii=False).encode('utf-8'))
        process.closeWriteChannel()

    def _models_error(self, process, error):
        if error == QProcess.ProcessError.FailedToStart:
            if process is self.models_process:
                self.models_process = None
                self.fetch_models_button.setEnabled(True)
                self.fetch_models_button.setText('获取模型列表')
                self.message.setText('模型列表查询进程未能启动；可使用自定义模型id。')
            process.deleteLater()

    def _models_finished(self, process, code, status):
        try:
            if process is not self.models_process:
                return
            if code or status != QProcess.ExitStatus.NormalExit:
                raise ValueError('模型列表查询未完成；可重试或使用自定义模型id。')
            result = json.loads(bytes(process.readAllStandardOutput()).decode('utf-8'))
            if not isinstance(result, dict):
                raise ValueError('模型列表响应格式无效')
            if not result.get('ok'):
                raise ValueError(result.get('message', '获取模型列表失败'))
            models = result.get('models')
            if not isinstance(models, list) or not all(isinstance(model, str) and model for model in models):
                raise ValueError('模型列表响应格式无效')
            selected = self._selected_model().strip()
            if selected not in models:
                self.model_edit.setText(selected)
            self._set_model_options(models, selected)
            self.message.setText(result.get('message', f'已获取 {len(models)} 个模型'))
        except (ValueError, UnicodeError, TypeError) as exc:
            self.message.setText(str(exc))
        finally:
            if process is self.models_process:
                self.models_process = None
                self.fetch_models_button.setEnabled(True)
                self.fetch_models_button.setText('获取模型列表')
            process.deleteLater()

    def done(self, result):
        self._cancel_models_request()
        super().done(result)

    def _rename(self, text):
        if self.current_id and self.profile_list.currentItem():
            self.profile_list.currentItem().setText(text.strip() or '未命名 API')

    def add_profile(self):
        item = self._append(new_profile())
        self.profile_list.setCurrentItem(item)
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def delete_profile(self):
        row = self.profile_list.currentRow()
        if row >= 0:
            # Commit the current draft before changing selection, then detach.
            self.profile_list.currentItem().setData(Qt.ItemDataRole.UserRole, self._draft())
            self.current_id = None
            self.profile_list.takeItem(row)
            if not self.profile_list.count():
                self._select(None)

    def save(self):
        if self.load_failed:
            return
        if self.profile_list.currentItem():
            self.profile_list.currentItem().setData(Qt.ItemDataRole.UserRole, self._draft())
        profiles = []
        for index in range(self.profile_list.count()):
            item = self.profile_list.item(index)
            profile = dict(item.data(Qt.ItemDataRole.UserRole))
            try:
                if isinstance(profile['extra_body'], str):
                    profile['extra_body'] = json.loads(profile['extra_body'] or '{}')
                profiles.append(normalize_profile(profile))
            except (ValueError, TypeError) as exc:
                self.profile_list.setCurrentRow(index)
                self.message.setText(f'“{item.text()}”配置无效：{exc}')
                return
        try:
            save_profiles(self.config_path, profiles)
        except (OSError, ValueError) as exc:
            self.message.setText(f'保存失败：{exc}')
            return
        self.accept()
