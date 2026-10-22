"""Qt editor for subject prompt templates, with save/cancel draft handling."""
from __future__ import annotations

import uuid

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from prompt_config import load_templates, save_templates


class PromptSettingsDialog(QDialog):
    def __init__(self, config_path, parent=None, selected_id=None):
        super().__init__(parent)
        self.config_path = config_path
        self.current_id = None
        self.setWindowTitle('设置提示词')
        self.resize(900, 650)
        self.setMinimumSize(720, 480)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.addWidget(QLabel('设置提示词', objectName='heading'))
        hint = QLabel('编辑各类作业的解题要求；题号映射、公式格式要求和分题标记由程序自动附加。', objectName='muted')
        hint.setWordWrap(True)
        outer.addWidget(hint)
        columns = QHBoxLayout()
        left = QVBoxLayout()
        self.template_list = QListWidget()
        self.template_list.setObjectName('apiProfiles')
        self.template_list.setMinimumWidth(180)
        self.template_list.setMaximumWidth(240)
        left.addWidget(self.template_list, 1)
        buttons = QHBoxLayout()
        self.add_button = QPushButton('添加')
        self.delete_button = QPushButton('删除')
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.delete_button)
        left.addLayout(buttons)
        columns.addLayout(left)
        self.editor = QWidget()
        right = QVBoxLayout(self.editor)
        right.setContentsMargins(8, 0, 0, 0)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText('提示词名称，例如数电')
        right.addWidget(self.name_edit)
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText('输入解题要求；无需填写题号和分题标记。')
        right.addWidget(self.text_edit, 1)
        columns.addWidget(self.editor, 1)
        outer.addLayout(columns, 1)
        self.message = QLabel('切换模板会保留编辑草稿；点击保存后写入本地配置。', objectName='muted')
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
        self.template_list.currentItemChanged.connect(self._select)
        self.name_edit.textChanged.connect(self._rename)
        self.add_button.clicked.connect(self.add_template)
        self.delete_button.clicked.connect(self.delete_template)
        self.load_failed = False
        try:
            templates = load_templates(config_path)
        except (OSError, ValueError, TypeError) as exc:
            templates = []
            self.load_failed = True
            self.message.setText(f'读取失败：{exc}；请修复配置文件后重新打开，原文件不会被覆盖。')
        self.save_button.setEnabled(not self.load_failed)
        self.add_button.setEnabled(not self.load_failed)
        selected = 0
        for index, template in enumerate(templates):
            self._append(template)
            if template['id'] == selected_id:
                selected = index
        if templates:
            self.template_list.setCurrentRow(selected)
        else:
            self._select(None)

    def _append(self, template):
        item = QListWidgetItem(template['name'])
        item.setData(Qt.ItemDataRole.UserRole, template)
        self.template_list.addItem(item)
        return item

    def _draft(self):
        return {'id': self.current_id, 'name': self.name_edit.text(), 'text': self.text_edit.toPlainText()}

    def _select(self, item, previous=None):
        if previous is not None and self.current_id:
            previous.setData(Qt.ItemDataRole.UserRole, self._draft())
        self.current_id = None
        self.editor.setEnabled(item is not None)
        self.delete_button.setEnabled(item is not None)
        template = item.data(Qt.ItemDataRole.UserRole) if item is not None else {}
        self.name_edit.setText(template.get('name', ''))
        self.text_edit.setPlainText(template.get('text', ''))
        self.current_id = template.get('id')

    def _rename(self, text):
        if self.current_id and self.template_list.currentItem() is not None:
            self.template_list.currentItem().setText(text.strip() or '未命名提示词')

    def add_template(self):
        item = self._append({'id': uuid.uuid4().hex, 'name': '新提示词', 'text': ''})
        self.template_list.setCurrentItem(item)
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def delete_template(self):
        row = self.template_list.currentRow()
        if row >= 0:
            self.current_id = None
            self.template_list.takeItem(row)
            if not self.template_list.count():
                self._select(None)

    def save(self):
        if self.load_failed:
            return
        item = self.template_list.currentItem()
        if item is not None:
            item.setData(Qt.ItemDataRole.UserRole, self._draft())
        templates = [self.template_list.item(index).data(Qt.ItemDataRole.UserRole)
                     for index in range(self.template_list.count())]
        try:
            save_templates(self.config_path, templates)
        except (OSError, ValueError, TypeError) as exc:
            self.message.setText(f'保存失败：{exc}')
            return
        self.accept()
