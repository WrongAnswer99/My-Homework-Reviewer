"""Qt desktop UI; conversion and image generation always run through the CLIs."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True

from PySide6.QtCore import QProcess, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QImage, QImageReader, QPalette, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListView, QListWidget, QListWidgetItem, QMainWindow, QPlainTextEdit, QProgressBar,
    QPushButton, QRadioButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from review_panel import ReviewPanel
from cli_progress import PROGRESS_PREFIX
from api_config import DEFAULT_CONFIG, load_profiles, normalize_profile
from api_homework import API_PREFIX
from api_settings_dialog import APISettingsDialog
from prompt_config import DEFAULT_CONFIG as DEFAULT_PROMPT_CONFIG, load_templates
from prompt_settings_dialog import PromptSettingsDialog


PROJECT = Path(__file__).resolve().parent
RENDER_DEFAULTS = {'mode': 'count', 'n': 5, 'max_height': 2400, 'width': 1280}
REASONING_OPTIONS = (('思考：默认', None), ('思考：关闭', 'none'), ('思考：极低', 'minimal'),
                     ('思考：低', 'low'), ('思考：中', 'medium'), ('思考：高', 'high'),
                     ('思考：极高', 'xhigh'), ('思考：最高', 'max'))


class ImageCardList(QListWidget):
    imageClicked = Signal(object)

    def _image_at(self, point):
        item = self.itemAt(point)
        if item is None:
            return None
        size = item.data(Qt.ItemDataRole.UserRole+2)
        if not isinstance(size, QSize):
            return None
        rect = self.visualItemRect(item)
        left = rect.left()+(rect.width()-size.width())//2
        top = rect.top()+12
        return item if left <= point.x() < left+size.width() and top <= point.y() < top+size.height() else None

    def mousePressEvent(self, event):
        self._image_pressed = self._image_at(event.position().toPoint()) if event.button() == Qt.MouseButton.LeftButton else None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        item = self._image_at(event.position().toPoint()) if event.button() == Qt.MouseButton.LeftButton else None
        super().mouseReleaseEvent(event)
        if item is not None and item is self._image_pressed:
            self.imageClicked.emit(item)
        self._image_pressed = None


class RenameTitle(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class RenameLineEdit(QLineEdit):
    cancelled = Signal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            return
        super().keyPressEvent(event)


STYLE = """
QMainWindow { background: #f5f7fa; }
QDialog { background: #f5f7fa; }
QWidget { font-family: 'Microsoft YaHei', 'Noto Sans SC', sans-serif; font-size: 14px; color: #243447; }
QFrame#sidebar { background: #edf1f6; border-right: 1px solid #dce3ed; }
QLabel#heading { font-size: 24px; font-weight: 600; }
QLabel#section { font-size: 16px; font-weight: 600; }
QLabel#muted { color: #66788b; font-size: 13px; }
QLabel#total { font-size: 20px; font-weight: 600; color: #204c79; }
QFrame#settings { background: white; border: 1px solid #dce3ed; border-radius: 10px; }
QPushButton { min-height: 34px; padding: 0 15px; border: 1px solid #ccd6e2;
              border-radius: 7px; background: white; }
QPushButton:hover { background: #eaf1fa; border-color: #7a9fc5; }
QPushButton:disabled { color: #66788b; background: #eef1f5; border-color: #dce3ed; }
QPushButton#primary { background: #2c6095; color: white; border-color: #2c6095; font-weight: 600; }
QPushButton#primary:hover { background: #244f7a; }
QPushButton#primary:disabled { color: #51677c; background: #dce3ed; border-color: #dce3ed; }
QListWidget#files { background: transparent; border: 0; outline: none; }
QListWidget#files::item { padding: 14px 10px; margin: 3px 0; border-radius: 7px; }
QListWidget#files::item:selected { background: #dbe8f6; color: #174b7d; }
QListWidget#files::item:hover { background: #e2eaf4; }
QListWidget#cards { background: transparent; border: 0; outline: none; }
QListWidget#cards::item { background: white; border: 1px solid #d9e2ed; border-radius: 10px; padding: 10px; }
QListWidget#cards::item:selected { background: #e6effa; color: #174b7d; border: 2px solid #2c6095; }
QListWidget#cards::item:hover { border-color: #7a9fc5; }
QTabWidget::pane { border: 0; }
QTabBar::tab { padding: 10px 22px; color: #51677c; background: transparent; border-bottom: 3px solid transparent; }
QTabBar::tab:selected { color: #255887; font-weight: 600; border-bottom: 3px solid #2c6095; }
QScrollArea { border: 0; background: transparent; }
QWidget#production { background: #f5f7fa; }
QWidget#apiEditor { background: #f5f7fa; }
QListWidget#questions { border: 0; background: transparent; outline: none; }
QListWidget#questions::item { border: 1px solid #dbe4ef; border-radius: 7px; background: white; }
QListWidget#questions::item:selected { background: #e1edfa; border-color: #5885b6; }
QPlainTextEdit { background: white; placeholder-text-color: #66788b;
                 selection-background-color: #2c6095; selection-color: white;
                 border: 1px solid #d4deea; border-radius: 9px; padding: 12px; }
QPlainTextEdit:focus { border-color: #4d80b4; }
QLineEdit { background: white; color: #243447; placeholder-text-color: #66788b;
            selection-background-color: #2c6095; selection-color: white;
            border: 1px solid #ccd6e2; border-radius: 5px; padding: 6px; min-height: 24px; }
QLineEdit:focus { border-color: #4d80b4; }
QLineEdit:disabled { color: #66788b; background: #f2f4f7; }
QListWidget#apiProfiles { background: white; border: 1px solid #ccd6e2; border-radius: 7px; outline: none; }
QListWidget#apiProfiles::item { padding: 12px 10px; color: #243447; background: white; }
QListWidget#apiProfiles::item:selected { background: #dbe8f6; color: #174b7d; }
QSpinBox, QDoubleSpinBox { background: white; selection-background-color: #2c6095; selection-color: white;
           border: 1px solid #ccd6e2; border-radius: 5px; padding: 5px; min-height: 25px; }
QSpinBox:disabled, QDoubleSpinBox:disabled { color: #66788b; background: #f2f4f7; }
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { subcontrol-origin: border; width: 20px;
    background: #edf1f6; border-left: 1px solid #ccd6e2; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-position: top right; border-top-right-radius: 5px; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-position: bottom right; border-bottom-right-radius: 5px; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover { background: #dbe8f6; }
QSpinBox::up-button:disabled, QSpinBox::down-button:disabled, QDoubleSpinBox::up-button:disabled, QDoubleSpinBox::down-button:disabled { background: #f2f4f7; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url('__UI_ASSETS__/chevron-up.svg'); width: 10px; height: 6px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url('__UI_ASSETS__/chevron-down.svg'); width: 10px; height: 6px; }
QRadioButton { spacing: 7px; }
QRadioButton:disabled, QCheckBox:disabled { color: #66788b; }
QRadioButton::indicator { width: 16px; height: 16px; border-radius: 9px;
                          border: 2px solid #73859a; background: white; }
QRadioButton::indicator:hover, QRadioButton::indicator:focus { border-color: #2c6095; }
QRadioButton::indicator:checked { border-color: #2c6095;
    background: qradialgradient(cx: 0.5, cy: 0.5, radius: 0.5, fx: 0.5, fy: 0.5,
                               stop: 0 #2c6095, stop: 0.5 #2c6095, stop: 0.6 white, stop: 1 white); }
QRadioButton::indicator:disabled { border-color: #9aa8b7; background: #f2f4f7; }
QRadioButton::indicator:checked:disabled {
    background: qradialgradient(cx: 0.5, cy: 0.5, radius: 0.5, fx: 0.5, fy: 0.5,
                               stop: 0 #66788b, stop: 0.5 #66788b, stop: 0.6 #f2f4f7, stop: 1 #f2f4f7); }
QCheckBox { spacing: 7px; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 3px;
                      border: 2px solid #73859a; background: white; }
QCheckBox::indicator:hover, QCheckBox::indicator:focus { border-color: #2c6095; }
QCheckBox::indicator:checked { image: url('__UI_ASSETS__/check.svg');
                              border-color: #2c6095; background: #2c6095; }
QCheckBox::indicator:disabled { border-color: #9aa8b7; background: #f2f4f7; }
QCheckBox::indicator:checked:disabled { background: #66788b; border-color: #66788b; }
QProgressBar { border: 0; background: #e0e8f1; border-radius: 3px;
              min-height: 18px; max-height: 20px; text-align: center; }
QProgressBar::chunk { background: #97b8da; border-radius: 3px; }
QComboBox { background: white; border: 1px solid #ccd6e2; border-radius: 7px;
            min-height: 34px; padding: 0 10px; }
QComboBox:hover, QComboBox:focus { border-color: #4d80b4; }
QComboBox:disabled { color: #66788b; background: #f2f4f7; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right;
                      width: 26px; border: none; }
QComboBox::down-arrow { image: url('__UI_ASSETS__/chevron-down.svg'); width: 10px; height: 6px; }
QComboBox QAbstractItemView { background: white; color: #243447;
    selection-background-color: #2c6095; selection-color: white;
    border: 1px solid #ccd6e2; outline: none; }
QComboBox QAbstractItemView::item { min-height: 28px; padding: 4px 10px;
                                  background: white; color: #243447; }
QComboBox QAbstractItemView::item:selected { background: #2c6095; color: white; }
QMenu { background: white; color: #243447; border: 1px solid #ccd6e2; }
QMenu::item { padding: 7px 20px; background: transparent; }
QMenu::item:selected { background: #2c6095; color: white; }
QMenu::item:disabled { color: #66788b; }
QMenu::separator { height: 1px; background: #dce3ed; margin: 4px 8px; }
QToolTip { background: #ffffff; color: #243447; border: 1px solid #ccd6e2; padding: 5px; }
QScrollBar:horizontal { background: #e1e8f2; height: 10px; border-radius: 5px; }
QScrollBar::handle:horizontal { background: #9bb0c8; min-width: 40px; border-radius: 5px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }
QScrollBar:vertical { background: #e1e8f2; width: 10px; border-radius: 5px; }
QScrollBar::handle:vertical { background: #9bb0c8; min-height: 30px; border-radius: 5px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
""".replace('__UI_ASSETS__', (PROJECT/'data/ui').as_posix())


def light_palette():
    """Keep unstyled Qt surfaces and indicators readable on dark Windows themes."""
    palette = QPalette()
    colors = {
        'Window': '#f5f7fa', 'WindowText': '#243447',
        'Base': '#ffffff', 'AlternateBase': '#edf1f6', 'Text': '#243447',
        'Button': '#ffffff', 'ButtonText': '#243447',
        'Highlight': '#2c6095', 'HighlightedText': '#ffffff',
        'ToolTipBase': '#ffffff', 'ToolTipText': '#243447',
        'PlaceholderText': '#66788b', 'Link': '#245d96', 'LinkVisited': '#635190',
        'Light': '#ffffff', 'Midlight': '#edf1f6', 'Mid': '#ccd6e2',
        'Dark': '#73859a', 'Shadow': '#51677c',
    }
    for role, color in colors.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(color))
    for role in ('WindowText', 'Text', 'ButtonText'):
        palette.setColor(QPalette.ColorGroup.Disabled, getattr(QPalette.ColorRole, role), QColor('#66788b'))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, QColor('#f2f4f7'))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight, QColor('#dbe8f6'))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.HighlightedText, QColor('#243447'))
    return palette


def scan_mhtml(directory):
    return sorted((p for p in Path(directory).iterdir()
                   if p.is_file() and p.suffix.lower() in {'.mhtml', '.mht'}),
                  key=lambda p: p.name.casefold())


def cli_command(script, arguments):
    python = Path(sys.executable)
    if python.name.lower() == 'pythonw.exe':
        python = python.with_name('python.exe')
    return str(python), ['-B', '-X', 'utf8', '-u', str(PROJECT/script), *map(str, arguments)]


class HomeworkWindow(QMainWindow):
    def __init__(self, directory=None, state_path=None, api_config_path=None, prompt_config_path=None):
        super().__init__()
        # Offscreen Qt previews do not automatically discover Windows fonts.
        # Register the same local Chinese font used by the desktop UI explicitly.
        app = QApplication.instance()
        if not app.property('homework_font_loaded'):
            font_path = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts/msyh.ttc'
            if font_path.exists():
                font_id = QFontDatabase.addApplicationFont(str(font_path))
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    app.setFont(QFont(families[0], 10))
            app.setProperty('homework_font_loaded', True)
        self.directory = Path(directory or Path.cwd()).resolve()
        self.state_path = Path(state_path or PROJECT/'data/gui_state.json')
        self.api_config_path = Path(api_config_path or DEFAULT_CONFIG)
        self.prompt_config_path = Path(prompt_config_path or DEFAULT_PROMPT_CONFIG)
        self.prompt_templates = []
        self.api_profiles = []
        try:
            self.state = json.loads(self.state_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.state = {}
        self.source = self.export_dir = self.render_dir = None
        self.report = None
        self.study_path = None
        self.answered = set()
        self.answer_dirty = False
        self.card_texts = {}
        self.pending_merges = {}
        self.api_protected_range = None
        self.current_card_key = None
        self.unassigned_text = ''
        self.close_requested = False
        self.job = None
        self.bulk_task = None
        self.last_api_request = None
        self.reasoning_process = None
        self.reasoning_parameter = 'reasoning_effort'
        self.log = b''
        self.output_pending = b''
        self.loading = False
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(500)
        self.save_timer.timeout.connect(self.save_state)
        self.answer_timer = QTimer(self)
        self.answer_timer.setSingleShot(True)
        self.answer_timer.setInterval(800)
        self.answer_timer.timeout.connect(self.start_merge)
        self.setWindowTitle('作业复习 · 资料制作、纠错补充与复习提问')
        self.resize(1320, 900)
        self.setMinimumSize(1020, 740)
        self.setPalette(light_palette())
        self.setStyleSheet(STYLE)
        self._build_ui()
        self._restore_render_settings()
        self.refresh_files()
        self._request_reasoning_info()

    def _render_settings(self):
        return {'mode': 'height' if self.height_mode.isChecked() else 'count',
                'n': self.n_spin.value(), 'max_height': self.height_spin.value(),
                'width': self.width_spin.value()}

    def _restore_render_settings(self):
        # Upgrade the last selected file's preferences once. Subsequent file
        # selections only restore that file's notes and output locations.
        files = self.state.get('files', {})
        legacy = files.get(self.state.get('selected_source'), {})
        saved = self.state.get('render_settings', legacy)
        settings = {key: saved.get(key, default) for key, default in RENDER_DEFAULTS.items()}
        self.loading = True
        self.height_mode.setChecked(settings['mode'] == 'height')
        self.count_mode.setChecked(settings['mode'] != 'height')
        self.n_spin.setValue(settings['n'])
        self.height_spin.setValue(settings['max_height'])
        self.width_spin.setValue(settings['width'])
        model_index = self.ocr_model_combo.findData(self.state.get('ocr_model', 'v4'))
        self.ocr_model_combo.setCurrentIndex(max(0, model_index))
        self._refresh_api_profiles()
        self._refresh_prompt_templates()
        self.loading = False
        self.state['render_settings'] = self._render_settings()
        for entry in files.values():
            for key in RENDER_DEFAULTS:
                entry.pop(key, None)

    def _build_ui(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)
        sidebar = QFrame()
        sidebar.setObjectName('sidebar')
        sidebar.setMinimumWidth(230)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 24, 18, 20)
        side.setSpacing(12)
        self.refresh_button = QPushButton('刷新文件列表')
        self.refresh_button.clicked.connect(self.refresh_files)
        side.addWidget(self.refresh_button)
        self.folder_button = QPushButton('选择文件夹…')
        self.folder_button.clicked.connect(self.choose_folder)
        side.addWidget(self.folder_button)
        self.directory_label = QLabel()
        self.directory_label.setObjectName('muted')
        self.directory_label.setWordWrap(True)
        side.addWidget(self.directory_label)
        self.file_count_label = QLabel()
        self.file_count_label.setObjectName('section')
        side.addWidget(self.file_count_label)
        self.file_list = QListWidget()
        self.file_list.setObjectName('files')
        self.file_list.currentItemChanged.connect(self._select_file)
        side.addWidget(self.file_list, 1)
        side.addWidget(QLabel('扫描当前文件夹，不含子文件夹。', objectName='muted'))
        splitter.addWidget(sidebar)

        right = QWidget()
        outer_layout = QVBoxLayout(right)
        outer_layout.setContentsMargins(28, 22, 28, 18)
        outer_layout.setSpacing(13)
        titlebar = QHBoxLayout()
        self.title_label = RenameTitle('请选择一个 MHTML 文件')
        self.title_label.setObjectName('heading')
        self.title_label.setToolTip('点击重命名当前 MHTML 文件')
        self.title_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.title_label.clicked.connect(self._begin_rename)
        titlebar.addWidget(self.title_label, 1)
        self.rename_input = RenameLineEdit()
        self.rename_input.setObjectName('renameFile')
        self.rename_input.setPlaceholderText('输入新文件名（无需扩展名）')
        self.rename_input.returnPressed.connect(self._commit_rename)
        self.rename_input.cancelled.connect(self._end_rename)
        self.rename_input.hide()
        titlebar.addWidget(self.rename_input, 1)
        self.rename_confirm_button = QPushButton('确定')
        self.rename_confirm_button.clicked.connect(self._commit_rename)
        self.rename_confirm_button.hide()
        titlebar.addWidget(self.rename_confirm_button)
        self.api_config_button = QPushButton('API 配置')
        self.api_config_button.clicked.connect(self.open_api_config)
        titlebar.addWidget(self.api_config_button)
        outer_layout.addLayout(titlebar)
        self.tabs = QTabWidget()
        outer_layout.addWidget(self.tabs, 1)
        production = QWidget()
        production.setObjectName('production')
        layout = QVBoxLayout(production)
        layout.setContentsMargins(0, 12, 0, 4)
        layout.setSpacing(13)
        production_scroll = QScrollArea()
        production_scroll.setWidgetResizable(True)
        production_scroll.setWidget(production)
        self.tabs.addTab(production_scroll, '资料制作')
        self.review_panel = ReviewPanel(cli_command, self, api_context=self._review_api_context,
                                        mode='review')
        self.review_panel.statusChanged.connect(self._status)
        self.review_panel.reasoningRequested.connect(lambda: self._request_reasoning_info(allow_probe=True))
        self.correction_panel = ReviewPanel(cli_command, self, api_context=self._review_api_context,
                                            mode='correction')
        self.correction_panel.statusChanged.connect(self._status)
        self.correction_panel.reasoningRequested.connect(lambda: self._request_reasoning_info(allow_probe=True))
        self.correction_panel.apiBusyChanged.connect(self._update_buttons)
        self.tabs.addTab(self.correction_panel, '纠错补充')
        self.tabs.addTab(self.review_panel, '复习提问')
        self.tabs.currentChanged.connect(self._tab_changed)
        toolbar = QHBoxLayout()
        self.convert_button = QPushButton('开始转换成 Markdown')
        self.convert_button.setObjectName('primary')
        self.convert_button.setToolTip('已有导出时生成新版本，保留原有 Markdown 和图片。')
        self.convert_button.clicked.connect(self.start_convert)
        toolbar.addWidget(self.convert_button)
        self.ocr_model_combo = QComboBox()
        self.ocr_model_combo.setView(QListView())
        self.ocr_model_combo.addItem('OCR · v4', 'v4')
        self.ocr_model_combo.addItem('OCR · v6 tiny', 'v6tiny')
        self.ocr_model_combo.setMinimumWidth(140)
        self.ocr_model_combo.setToolTip('选择本地 OCR 模型；所有文件共用，默认 v4。')
        self.ocr_model_combo.currentIndexChanged.connect(self._queue_save)
        toolbar.addWidget(self.ocr_model_combo)
        toolbar.addStretch()
        self.open_markdown_button = QPushButton('打开 Markdown')
        self.open_markdown_button.clicked.connect(lambda: self.open_output(False))
        toolbar.addWidget(self.open_markdown_button)
        self.open_folder_button = QPushButton('打开所在文件夹')
        self.open_folder_button.clicked.connect(lambda: self.open_output(True))
        toolbar.addWidget(self.open_folder_button)
        layout.addLayout(toolbar)
        self.total_label = QLabel('尚未转换')
        self.total_label.setObjectName('total')
        layout.addWidget(self.total_label)

        settings = QFrame()
        settings.setObjectName('settings')
        config = QVBoxLayout(settings)
        config.setContentsMargins(16, 14, 16, 14)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.count_mode = QRadioButton('每页题数')
        self.height_mode = QRadioButton('最大图片高度')
        modes = QButtonGroup(self)
        modes.addButton(self.count_mode)
        modes.addButton(self.height_mode)
        self.count_mode.setChecked(True)
        self.n_spin = QSpinBox()
        self.n_spin.setRange(1, 1000)
        self.n_spin.setValue(5)
        self.n_spin.setSuffix(' 题')
        self.height_spin = QSpinBox()
        self.height_spin.setRange(100, 100000)
        self.height_spin.setSingleStep(100)
        self.height_spin.setValue(2400)
        self.height_spin.setSuffix(' px')
        row.addWidget(self.count_mode)
        row.addWidget(self.n_spin)
        row.addSpacing(8)
        row.addWidget(self.height_mode)
        row.addWidget(self.height_spin)
        row.addStretch()
        config.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel('图片宽度'))
        self.width_spin = QSpinBox()
        self.width_spin.setRange(480, 4096)
        self.width_spin.setSingleStep(80)
        self.width_spin.setValue(1280)
        self.width_spin.setSuffix(' px')
        row2.addWidget(self.width_spin)
        row2.addWidget(QLabel('所有作业共用设置；超高单题单独成图。', objectName='muted'))
        row2.addStretch()
        self.render_button = QPushButton('生成图片')
        self.render_button.setObjectName('primary')
        self.render_button.clicked.connect(self.start_render)
        row2.addWidget(self.render_button)
        config.addLayout(row2)
        layout.addWidget(settings)
        for radio in (self.count_mode, self.height_mode):
            radio.toggled.connect(self._settings_changed)
        for spin in (self.n_spin, self.height_spin, self.width_spin):
            spin.valueChanged.connect(self._settings_changed)

        cards_header = QHBoxLayout()
        self.cards_label = QLabel('题目图片')
        self.cards_label.setObjectName('section')
        cards_header.addWidget(self.cards_label)
        cards_header.addStretch()
        self.copy_prompt_button = QPushButton('复制提示词')
        self.copy_prompt_button.setToolTip('复制当前选中卡片的解题提示词及题目标识。')
        self.copy_prompt_button.clicked.connect(self.copy_answer_prompt)
        cards_header.addWidget(self.copy_prompt_button)
        self.prompt_template_combo = QComboBox()
        self.prompt_template_combo.setView(QListView())
        self.prompt_template_combo.setMinimumContentsLength(6)
        self.prompt_template_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.prompt_template_combo.setToolTip('选择复制和 API 调用时使用的解题提示词')
        self.prompt_template_combo.currentIndexChanged.connect(self._queue_save)
        cards_header.addWidget(self.prompt_template_combo)
        self.prompt_settings_button = QPushButton('设置提示词')
        self.prompt_settings_button.clicked.connect(self.open_prompt_settings)
        cards_header.addWidget(self.prompt_settings_button)
        layout.addLayout(cards_header)
        self.cards = ImageCardList()
        self.cards.setObjectName('cards')
        self.cards.setViewMode(QListView.ViewMode.IconMode)
        self.cards.setFlow(QListView.Flow.LeftToRight)
        self.cards.setWrapping(False)
        self.cards.setMovement(QListView.Movement.Static)
        self.cards.setResizeMode(QListView.ResizeMode.Adjust)
        self.cards.setIconSize(QSize(176, 185))
        self.cards.setGridSize(QSize(216, 280))
        self.cards.setWordWrap(False)
        self.cards.setSpacing(6)
        self.cards.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cards.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.cards.setFixedHeight(302)
        self.cards.imageClicked.connect(self.copy_card)
        self.cards.currentItemChanged.connect(self._card_changed)
        layout.addWidget(self.cards)

        answer_area = QHBoxLayout()
        answer_area.setSpacing(16)
        paste_panel = QWidget()
        paste_layout = QVBoxLayout(paste_panel)
        paste_layout.setContentsMargins(0, 0, 0, 0)
        paste_layout.setSpacing(10)
        text_header = QHBoxLayout()
        text_header.addWidget(QLabel('粘贴 AI 的 Markdown 解答', objectName='section'))
        text_header.addStretch()
        self.answer_count_label = QLabel('已导入 0 题', objectName='muted')
        text_header.addWidget(self.answer_count_label)
        paste_layout.addLayout(text_header)
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText('先复制当前卡片的提示词，再点击缩略图复制题图，粘贴到 AI 聊天。\n'
                                       '把这张卡片的完整 Markdown 解答粘贴到这里；切换卡片后会显示各自的草稿。')
        self.prompt.setMinimumHeight(210)
        self.prompt.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.prompt.textChanged.connect(self._queue_save)
        self.prompt.textChanged.connect(self._answer_changed)
        paste_layout.addWidget(self.prompt, 1)
        answer_area.addWidget(paste_panel, 1)

        api_panel = QFrame()
        api_panel.setObjectName('settings')
        api_layout = QVBoxLayout(api_panel)
        api_layout.setContentsMargins(16, 14, 16, 14)
        api_layout.setSpacing(8)
        api_layout.addWidget(QLabel('使用 API', objectName='section'))
        api_layout.addWidget(QLabel('模型'))

        self.api_combo = QComboBox()
        self.api_combo.setView(QListView())
        self.api_combo.setMinimumWidth(160)
        self.api_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.api_combo.setMinimumContentsLength(12)
        self.api_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.api_combo.currentIndexChanged.connect(self._api_selection_changed)
        api_layout.addWidget(self.api_combo)
        api_controls = QHBoxLayout()
        self.call_api_button = QPushButton('直接调用 API')
        self.call_api_button.clicked.connect(self.start_api)
        self.call_api_button.setToolTip('发送当前选中卡片的题目和提示词，完整解答返回后自动整理。')
        api_controls.addWidget(self.call_api_button)
        self.bulk_api_button = QPushButton('为所有题目依次调用 API')
        self.bulk_api_button.setStyleSheet('padding: 0 8px; font-size: 13px;')
        self.bulk_api_button.setToolTip('按当前作业的图片顺序独立请求，已完成整组自动跳过；失败暂停，可继续。')
        self.bulk_api_button.clicked.connect(self.start_bulk_api)
        api_controls.addWidget(self.bulk_api_button)
        api_layout.addLayout(api_controls)
        retry_controls = QHBoxLayout()
        self.retry_api_button = QPushButton('重试请求')
        self.retry_api_button.clicked.connect(self.retry_api)
        self.retry_api_button.setToolTip('重新发送上次选中卡片的请求，沿用当时的 API 和思考档位。')
        retry_controls.addWidget(self.retry_api_button)
        retry_controls.addStretch()
        api_layout.addLayout(retry_controls)
        self.bulk_label = QLabel('依次处理当前作业；每组成功后立即保存。', objectName='muted')
        self.bulk_label.setWordWrap(True)
        api_layout.addWidget(self.bulk_label)
        self.bulk_progress = QProgressBar()
        self.bulk_progress.hide()
        api_layout.addWidget(self.bulk_progress)

        reasoning_controls = QHBoxLayout()
        reasoning_controls.addWidget(QLabel('思考强度'))
        reasoning_controls.addStretch()
        self.reasoning_combo = QComboBox()
        self.reasoning_combo.setView(QListView())
        for label, effort in REASONING_OPTIONS:
            self.reasoning_combo.addItem(label, effort)
        self.reasoning_combo.setMinimumWidth(112)
        self.reasoning_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.reasoning_combo.setToolTip('本次 API 请求的思考强度；“默认”沿用服务端或配置。'
                                       '选择具体档位会覆盖配置中的思考开关或预算，不修改 Temperature。')
        self.reasoning_hint = QLabel('档位待查询', objectName='muted')
        reasoning_controls.addWidget(self.reasoning_hint)
        api_layout.addLayout(reasoning_controls)
        reasoning_actions = QHBoxLayout()
        reasoning_actions.setSpacing(8)
        reasoning_actions.addWidget(self.reasoning_combo, 1)
        self.retry_reasoning_button = QPushButton('重试获取思考强度')
        self.retry_reasoning_button.clicked.connect(lambda: self._request_reasoning_info(allow_probe=True))
        self.retry_reasoning_button.setToolTip('重新查询模型目录；缺少档位时发送一次最小测试请求。'
                                               '不发送题目，可能产生少量 API 费用。')
        reasoning_actions.addWidget(self.retry_reasoning_button)
        api_layout.addLayout(reasoning_actions)
        api_layout.addStretch()
        answer_area.addWidget(api_panel, 1)
        layout.addLayout(answer_area, 1)
        answer_footer = QHBoxLayout()
        self.batch_label = QLabel('请选择图片卡片后复制提示词', objectName='muted')
        answer_footer.addWidget(self.batch_label, 1)
        self.open_study_button = QPushButton('打开整理后的文档')
        self.open_study_button.setObjectName('primary')
        self.open_study_button.clicked.connect(self.open_study)
        answer_footer.addWidget(self.open_study_button)
        layout.addLayout(answer_footer)
        footer = QHBoxLayout()
        self.status_label = QLabel('就绪')
        self.status_label.setWordWrap(True)
        footer.addWidget(self.status_label, 1)
        self.log_button = QPushButton('运行日志')
        self.log_button.clicked.connect(self.show_log)
        footer.addWidget(self.log_button)
        outer_layout.addLayout(footer)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        outer_layout.addWidget(self.progress)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 1060])

    def _tab_changed(self, index):
        panel = self.tabs.widget(index)
        if panel not in (self.review_panel, self.correction_panel):
            return
        if panel.active and panel.export_dir is not None and panel.api_job is None:
            panel.set_export(panel.export_dir, refresh=True)
        panel.activate()

    def _begin_rename(self):
        if self.source is None or self.job is not None or self.correction_panel.api_job is not None or self._bulk_running():
            return
        self.rename_input.setText(self.source.stem)
        self.title_label.hide()
        self.rename_input.show()
        self.rename_confirm_button.show()
        self.rename_input.setFocus()
        self.rename_input.selectAll()

    def _end_rename(self):
        self.rename_input.hide()
        self.rename_confirm_button.hide()
        self.title_label.show()

    def _export_source_name(self):
        if self.source is None:
            return None
        if self.report is not None:
            return self.report['source_file']
        entry = self.state.get('files', {}).get(str(self.source), {})
        return entry.get('source_file') or self.source.name

    def _commit_rename(self):
        if self.source is None or self.job is not None or self.correction_panel.api_job is not None or self._bulk_running():
            return
        source = self.source
        base = self.rename_input.text().strip()
        if not base or base.endswith('.'):
            self._status('请输入有效的 .mhtml 或 .mht 文件名。', True)
            return
        suffix = source.suffix
        if base.lower().endswith(('.mhtml', '.mht')):
            suffix = Path(base).suffix
            base = base[:-len(suffix)]
        name = base+suffix
        if (not base or base in {'.', '..'} or base.endswith(('.', ' '))
                or any(character in name for character in '<>:"/\\|?*')
                or any(ord(character) < 32 for character in name)
                or Path(name).suffix.lower() not in {'.mhtml', '.mht'}):
            self._status('请输入有效的 .mhtml 或 .mht 文件名。', True)
            return
        if name == source.name:
            self._end_rename()
            return
        target = source.with_name(name)
        try:
            if target.exists() and not target.samefile(source):
                self._status('同名文件已存在，请换一个名称。', True)
                return
            self.save_timer.stop()
            self.save_state()
            source.rename(target)
        except OSError as exc:
            self._status(f'重命名失败：{exc}', True)
            return
        files = self.state.setdefault('files', {})
        entry = files.pop(str(source), {})
        entry['source_file'] = self.report['source_file'] if self.report else entry.get('source_file', source.name)
        files[str(target)] = entry
        self.state['selected_source'] = str(target)
        self.source = target
        self.refresh_files()
        self._status(f'已重命名为 {target.name}。')

    def refresh_files(self):
        if self.job or self._bulk_running():
            return
        self._end_rename()
        previous = str(self.source) if self.source else self.state.get('selected_source')
        try:
            files = scan_mhtml(self.directory)
        except OSError as exc:
            self._status(f'无法扫描文件夹：{exc}', True)
            return
        self.file_list.blockSignals(True)
        self.file_list.clear()
        selected = 0
        for index, path in enumerate(files):
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path.resolve()))
            item.setToolTip(str(path))
            self.file_list.addItem(item)
            if str(path.resolve()) == previous:
                selected = index
        self.file_list.blockSignals(False)
        self.directory_label.setText(str(self.directory))
        self.file_count_label.setText(f'{len(files)} 个 MHTML 文件')
        if files:
            self.file_list.setCurrentRow(selected)
        else:
            self._select_file(None)
            self._status('当前文件夹没有 MHTML 文件。')

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, '选择保存 MHTML 的文件夹', str(self.directory))
        if path:
            self.save_state()
            self.directory = Path(path).resolve()
            self.refresh_files()

    def _select_file(self, item, previous=None):
        self.bulk_task = None
        self.bulk_progress.hide()
        self.bulk_label.setText('依次处理当前作业；每组成功后立即保存。')
        self._end_rename()
        self.save_state()
        self.answer_timer.stop()
        self.answer_dirty = False
        self.loading = True
        self.current_card_key = None
        self.card_texts = {}
        self.unassigned_text = ''
        self.source = Path(item.data(Qt.ItemDataRole.UserRole)) if item else None
        self.export_dir = self.render_dir = self.report = None
        self.review_panel.set_export(None)
        self.correction_panel.set_export(None)
        self.study_path = None
        self.answered = set()
        self._show_answer_count()
        self.cards.clear()
        self.cards_label.setText('题目图片 · 尚未生成')
        if self.source is None:
            self.title_label.setText('请选择一个 MHTML 文件')
            self.total_label.setText('尚未转换')
            self.prompt.clear()
        else:
            self.title_label.setText(self.source.stem)
            entry = self.state.get('files', {}).get(str(self.source), {})
            stored = entry.get('card_texts', {})
            if isinstance(stored, dict):
                self.card_texts = {key: value for key, value in stored.items()
                                   if isinstance(key, str) and isinstance(value, str)}
            self.unassigned_text = entry.get('text', '')
            self.prompt.setPlainText(self.unassigned_text)
            default = self.directory/'exports'/self.source.stem
            candidate = Path(entry.get('export_dir') or default)
            self._load_export(candidate)
            if self.export_dir:
                candidate = Path(entry.get('render_dir') or self.export_dir/'rendered')
                self._load_cards(candidate)
            self._status('选择图片卡片即可复制，转换和生成图片可分别执行。')
        self.loading = False
        self._update_buttons()
        self._queue_save()
        self._answer_changed()

    def _load_export(self, directory, expected_source=None):
        try:
            report = json.loads((directory/'questions.json').read_text(encoding='utf-8'))
            if report['source_file'] != (expected_source or self._export_source_name()) or not (directory/'questions.md').is_file():
                raise ValueError('导出与所选文件不匹配')
            self.report, self.export_dir = report, directory
            self.study_path = None
            self.answered = set()
            store_path = directory/'answers.json'
            if store_path.is_file():
                try:
                    store = json.loads(store_path.read_text(encoding='utf-8'))
                    self.answered = {int(key.rsplit('-Q', 1)[1]) for key in store['answers']}
                    document = directory/'questions_with_answers.md'
                    if document.is_file():
                        self.study_path = document
                except (OSError, ValueError, KeyError, TypeError):
                    self.answered = set()
            self._show_answer_count()
            self.total_label.setText(f'共 {report["question_count"]} 题 · {report["assignment_count"]} 份作业')
            self.review_panel.set_export(directory)
            self.correction_panel.set_export(directory)
        except (OSError, ValueError, KeyError):
            self.total_label.setText('尚未转换 · 请先生成 Markdown')

    def _load_cards(self, directory):
        desired_card = self.current_card_key
        if desired_card is None and self.source is not None:
            desired_card = self.state.get('files', {}).get(str(self.source), {}).get('selected_card')
        self.cards.clear()
        self.render_dir = None
        try:
            manifest = json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
            if manifest['source_file'] != self._export_source_name():
                raise ValueError('图片与所选文件不匹配')
            for page in manifest['pages']:
                path = (directory/page['image']).resolve()
                if not path.is_relative_to(directory.resolve()):
                    raise ValueError('无效的图片路径')
                reader = QImageReader(str(path))
                original = reader.size()
                reader.setScaledSize(original.scaled(self.cards.iconSize(), Qt.AspectRatioMode.KeepAspectRatio))
                image = reader.read()
                if image.isNull():
                    raise ValueError(f'无法读取图片：{path.name}')
                label = f'第 {page["first"]}–{page["last"]} 题'
                extra = '单题超高' if page.get('oversize') else f'{page["question_count"]} 题'
                item = QListWidgetItem(QIcon(QPixmap.fromImage(image)), label+f'\n{extra} · {page["height"]} px')
                item.setSizeHint(QSize(204, 278))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setData(Qt.ItemDataRole.UserRole, str(path))
                item.setData(Qt.ItemDataRole.UserRole+1, page)
                item.setData(Qt.ItemDataRole.UserRole+2, image.size())
                item.setToolTip(f'{label} · {page["width"]} × {page["height"]} px\n点击缩略图复制图片；点击题号只选中')
                self.cards.addItem(item)
            self.render_dir = directory
            rule = (f'最大高度 {manifest["max_height"]} px' if manifest.get('max_height') is not None
                    else f'每张 {manifest["questions_per_image"]} 题')
            self.cards_label.setText(f'{len(manifest["pages"])} 张图片 · {rule}')
            if self.cards.count():
                selected = next((index for index in range(self.cards.count())
                                 if self._card_key(self.cards.item(index)) == desired_card), 0)
                self.cards.setCurrentRow(selected)
            self._show_answer_count()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.cards.clear()
            self.cards_label.setText('题目图片 · 尚未生成')
            if (directory/'manifest.json').exists():
                self._status(f'图片列表读取失败：{exc}', True)

    def _settings_changed(self, *args):
        self._update_buttons()
        self._queue_save()

    def _update_buttons(self):
        bulk_running = self._bulk_running()
        idle = self.job is None and self.correction_panel.api_job is None and not bulk_running
        selected = self.source is not None
        for widget in (self.refresh_button, self.folder_button, self.file_list):
            widget.setEnabled(idle)
        self.convert_button.setEnabled(idle and selected)
        self.title_label.setEnabled(idle and selected)
        self.rename_input.setEnabled(idle and selected)
        self.rename_confirm_button.setEnabled(idle and selected)
        self.ocr_model_combo.setEnabled(idle and selected)
        self.render_button.setEnabled(idle and self.export_dir is not None)
        self.open_markdown_button.setEnabled(self.export_dir is not None)
        self.open_folder_button.setEnabled(self.export_dir is not None)
        for widget in (self.count_mode, self.height_mode, self.width_spin):
            widget.setEnabled(idle and selected)
        self.n_spin.setEnabled(idle and selected and self.count_mode.isChecked())
        self.height_spin.setEnabled(idle and selected and self.height_mode.isChecked())
        api_running = self.job is not None and self.job['kind'] == 'api'
        self.prompt.setEnabled(selected)
        self.prompt.setReadOnly(api_running and self.current_card_key == self.job['card_key'])
        self.cards.setEnabled(idle or api_running or bulk_running)
        has_prompt = self.prompt_template_combo.currentData() is not None
        self.copy_prompt_button.setEnabled(idle and has_prompt and self.export_dir is not None and self.cards.currentItem() is not None)
        self.prompt_template_combo.setEnabled(idle and bool(self.prompt_templates))
        self.prompt_settings_button.setEnabled(idle)
        self.open_study_button.setEnabled(idle and self.study_path is not None and self.study_path.is_file())
        self.api_config_button.setEnabled(not api_running and not bulk_running)
        self.api_combo.setEnabled(idle and bool(self.api_profiles))
        self.reasoning_combo.setEnabled(idle and bool(self.api_profiles))
        self.retry_reasoning_button.setEnabled(idle and self.api_combo.currentData() is not None)
        self.call_api_button.setText('停止请求' if api_running else '直接调用 API')
        self.call_api_button.setEnabled(not bulk_running and (api_running or (idle and self.export_dir is not None
                                        and self.cards.currentItem() is not None
                                        and self.api_combo.currentData() is not None and has_prompt)))
        self.bulk_api_button.setText('停止批量调用' if bulk_running else
                                    '继续批量调用' if self.bulk_task else '为所有题目依次调用 API')
        self.bulk_api_button.setEnabled(bulk_running or (idle and selected and has_prompt
                                         and self.api_combo.currentData() is not None))
        previous = self.last_api_request
        self.retry_api_button.setEnabled(bool(idle and previous and self.export_dir == previous['export_dir']
                                               and Path(previous['image']).is_file()
                                               and self._request_card_index(previous) >= 0
                                               and any(p['id'] == previous['profile'] for p in self.api_profiles)))
        self.correction_panel._buttons()

    def _refresh_api_profiles(self):
        selected = self.api_combo.currentData() or self.state.get('selected_api')
        error = None
        try:
            self.api_profiles = load_profiles(self.api_config_path)
        except (OSError, ValueError, TypeError) as exc:
            self.api_profiles = []
            error = str(exc)
        self.api_combo.blockSignals(True)
        self.api_combo.clear()
        for profile in self.api_profiles:
            self.api_combo.addItem(profile['name'], profile['id'])
        if not self.api_profiles:
            self.api_combo.addItem('请先配置 API', None)
        else:
            self.api_combo.setCurrentIndex(max(0, self.api_combo.findData(selected)))
        self.api_combo.blockSignals(False)
        self.api_combo.setToolTip('API 配置读取失败：'+error if error else '所有作业共用的 API 选择；发送当前选中卡片。')
        self.review_panel.sync_api_options()
        self.correction_panel.sync_api_options()

    def _review_api_context(self):
        return {'profiles': [(self.api_combo.itemText(i), self.api_combo.itemData(i))
                             for i in range(self.api_combo.count())],
                'profile': self.api_combo.currentData(),
                'efforts': [(self.reasoning_combo.itemText(i), self.reasoning_combo.itemData(i))
                            for i in range(self.reasoning_combo.count())],
                'effort': self.reasoning_combo.currentData(),
                'parameter': self.reasoning_parameter, 'config': self.api_config_path,
                'batch_running': self._bulk_running() or bool(self.job and self.job.get('bulk_position') is not None)}

    def _api_selection_changed(self, *args):
        self.reasoning_combo.setCurrentIndex(0)
        self.reasoning_parameter = 'reasoning_effort'
        self._queue_save()
        self._update_buttons()
        self._request_reasoning_info()

    def _request_reasoning_info(self, *, allow_probe=False):
        current = self.reasoning_process
        if current is not None and current.state() != QProcess.ProcessState.NotRunning:
            current.kill()
        self.reasoning_process = None
        selected_effort = self.reasoning_combo.currentData()
        self.reasoning_combo.blockSignals(True)
        self.reasoning_combo.clear()
        for label, effort in REASONING_OPTIONS:
            self.reasoning_combo.addItem(label, effort)
        self.reasoning_combo.setCurrentIndex(max(0, self.reasoning_combo.findData(selected_effort)))
        self.reasoning_combo.blockSignals(False)
        profile_id = self.api_combo.currentData()
        if not profile_id:
            self.reasoning_hint.setText('未配置')
            return
        self.reasoning_hint.setText('查询档位中…')
        self.reasoning_combo.setToolTip('正在查询模型目录'+('并按需进行最小探测' if allow_probe else '')+
                                       '；查询期间可以手动选择思考强度。')
        process = QProcess(self)
        self.reasoning_process = process
        process.finished.connect(lambda code, status, p=process, key=profile_id:
                                 self._reasoning_info_finished(p, key, code, status))
        process.errorOccurred.connect(lambda error, p=process, key=profile_id:
                                      self._reasoning_info_error(p, key, error))
        arguments = ['--config', self.api_config_path, '--profile', profile_id]
        if allow_probe:
            arguments.append('--probe')
        program, args = cli_command('api_reasoning.py', arguments)
        process.start(program, args)

    def _reasoning_info_error(self, process, profile_id, error):
        if error == QProcess.ProcessError.FailedToStart and process is self.reasoning_process:
            self.reasoning_process = None
            self.reasoning_hint.setText('档位未提供')
            self.reasoning_combo.setToolTip('模型信息查询进程未能启动；可以手动选择思考强度。')
            process.deleteLater()

    def _reasoning_info_finished(self, process, profile_id, code, status):
        try:
            if process is not self.reasoning_process or profile_id != self.api_combo.currentData():
                return
            if code or status != QProcess.ExitStatus.NormalExit:
                raise ValueError('模型信息查询未完成')
            result = json.loads(bytes(process.readAllStandardOutput()).decode('utf-8'))
            if not isinstance(result, dict):
                raise ValueError('模型信息格式无效')
            levels = result.get('levels', [])
            parameter = result.get('parameter', 'reasoning_effort')
            if parameter not in {'reasoning', 'reasoning_effort'} or not isinstance(levels, list):
                raise ValueError('模型信息格式无效')
            self.reasoning_parameter = parameter
            if result.get('status') == 'levels' and levels:
                selected_effort = self.reasoning_combo.currentData()
                self.reasoning_combo.blockSignals(True)
                self.reasoning_combo.clear()
                for label, effort in REASONING_OPTIONS:
                    if effort is None or effort in levels:
                        self.reasoning_combo.addItem(label, effort)
                self.reasoning_combo.setCurrentIndex(max(0, self.reasoning_combo.findData(selected_effort)))
                self.reasoning_combo.blockSignals(False)
                self.reasoning_hint.setText(f'{len(levels)} 档可选')
            elif result.get('status') == 'blocked':
                self.reasoning_hint.setText('服务商拦截查询')
                self._status(f'获取思考强度失败：{result.get("message", "服务商拒绝访问")}', True)
            else:
                self.reasoning_hint.setText('档位未提供')
            self.reasoning_combo.setToolTip(result.get('message', '模型接口未提供思考强度信息')+
                                            '；可选“默认”沿用服务端设置。')
        except (ValueError, UnicodeError, TypeError) as exc:
            self.reasoning_hint.setText('档位未提供')
            self.reasoning_combo.setToolTip(f'{exc}；可手动选择思考强度。')
        finally:
            if process is self.reasoning_process:
                self.reasoning_process = None
            self.review_panel.sync_api_options()
            self.correction_panel.sync_api_options()
            process.deleteLater()

    def open_api_config(self):
        dialog = APISettingsDialog(self.api_config_path, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._refresh_api_profiles()
            self._request_reasoning_info()
            self._update_buttons()
            self.save_state()
            self._status('API 配置已保存。选择图片卡片后可直接调用。')

    def _refresh_prompt_templates(self):
        selected = self.prompt_template_combo.currentData() or self.state.get('selected_prompt_template', 'analog')
        try:
            self.prompt_templates = load_templates(self.prompt_config_path)
        except (OSError, ValueError, TypeError) as exc:
            self.prompt_templates = []
            self._status(f'读取提示词配置失败：{exc}', True)
        self.prompt_template_combo.blockSignals(True)
        self.prompt_template_combo.clear()
        for template in self.prompt_templates:
            self.prompt_template_combo.addItem(template['name'], template['id'])
        self.prompt_template_combo.setCurrentIndex(max(0, self.prompt_template_combo.findData(selected)))
        self.prompt_template_combo.blockSignals(False)

    def open_prompt_settings(self):
        dialog = PromptSettingsDialog(self.prompt_config_path, self,
                                      selected_id=self.prompt_template_combo.currentData())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._refresh_prompt_templates()
            self._update_buttons()
            self.save_state()
            self._status('提示词已保存。复制提示词和 API 调用将使用选中的模板。')

    def _selected_prompt_text(self):
        selected = self.prompt_template_combo.currentData()
        template = next((item for item in self.prompt_templates if item['id'] == selected), None)
        if template is None:
            raise ValueError('请先设置并选择提示词模板')
        return template['text']

    @staticmethod
    def _snapshot_prompt(text):
        with tempfile.NamedTemporaryFile(mode='w', prefix='homework-prompt-', suffix='.txt',
                                         encoding='utf-8', delete=False) as temporary:
            temporary.write(text)
        return Path(temporary.name)

    def _bulk_running(self):
        return bool(self.bulk_task and self.bulk_task['running'])

    def _bulk_message(self, note):
        task = self.bulk_task
        if task is None:
            return
        task['note'] = note
        plan = task.get('plan')
        self.bulk_progress.show()
        if plan:
            total = len(plan['cards'])
            self.bulk_progress.setTextVisible(True)
            self.bulk_progress.setRange(0, total)
            self.bulk_progress.setValue(task['position'])
            self.bulk_progress.setFormat('已处理 %v / %m 组')
            done = len(set(plan['answered']) | self.answered)
            self.bulk_label.setText(f'{note}\n已解答 {done}/{plan["question_count"]} 题'
                                    f' · 已跳过 {task["skipped"]} 组')
        else:
            self.bulk_progress.setTextVisible(not task['running'])
            self.bulk_progress.setRange(0, 0 if task['running'] else 1)
            if not task['running']:
                self.bulk_progress.setValue(0)
                self.bulk_progress.setFormat('已暂停')
            self.bulk_label.setText(note)

    def _pause_bulk(self, reason):
        if self.bulk_task:
            self.bulk_task['running'] = False
            self._bulk_message('批量已暂停：'+reason)
            self._update_buttons()

    def start_bulk_api(self):
        if self._bulk_running():
            self._pause_bulk('已停止；再次点击可从未完成处继续。')
            if self.job and self.job['kind'] == 'api':
                self.job['cancelled'] = True
                self.process.kill()
            return
        if self.job or self.source is None or self.correction_panel.api_job is not None:
            return
        try:
            profile = next((p for p in load_profiles(self.api_config_path)
                            if p['id'] == self.api_combo.currentData()), None)
            if profile is None:
                raise ValueError('请先添加并选择 API 配置')
            profile = normalize_profile(profile, require_ready=True)
            base_request = {'profile': profile['id'], 'profile_snapshot': profile,
                            'reasoning_effort': self.reasoning_combo.currentData(),
                            'reasoning_parameter': self.reasoning_parameter,
                            'prompt_text': self._selected_prompt_text()}
        except (OSError, ValueError, TypeError) as exc:
            self._status(f'无法开始批量调用：{exc}', True)
            return
        task = self.bulk_task
        same_work = bool(task and task['source'] == self.source and
                         task['export_dir'] == self.export_dir and task['render_dir'] == self.render_dir)
        if same_work:
            task.update(running=True, position=0, skipped=0, phase='prepare', base_request=base_request)
        else:
            self.bulk_task = {'source': self.source, 'export_dir': self.export_dir,
                              'render_dir': self.render_dir, 'running': True, 'phase': 'prepare',
                              'position': 0, 'skipped': 0, 'plan': None, 'pending_result': None,
                              'base_request': base_request}
        self._bulk_message('正在准备当前作业的全部题目…')
        self._update_buttons()
        QTimer.singleShot(0, self._advance_bulk)

    def _advance_bulk(self):
        if not self._bulk_running() or self.job or self.correction_panel.api_job is not None:
            return
        task = self.bulk_task
        if self.source != task['source']:
            self._pause_bulk('当前作业已改变，请重新开始。')
            return
        self._drain_pending_merges()
        if self.job:
            return
        pending = task.get('pending_result')
        if pending:
            self._bulk_message('正在保存已返回的答案…')
            if not self._start_merge_text(pending['text'], pending['source'], api_result=True,
                                          exclude_orders=pending['keep_orders'], bulk_position=pending['position']):
                self._pause_bulk('无法保存已返回答案；继续时会先重试保存。')
            return
        if self.export_dir is None:
            self._bulk_message('正在自动转换 Markdown…')
            self.start_convert()
        elif not self.cards.count():
            self._bulk_message('正在按当前设置自动生成图片…')
            self.start_render()
        elif task['phase'] == 'dispatch':
            plan = task['plan']
            while task['position'] < len(plan['cards']):
                card = plan['cards'][task['position']]
                if card['complete']:
                    task['position'] += 1
                    task['skipped'] += 1
                    continue
                request = dict(task['base_request'], export_dir=self.export_dir,
                               first=card['first'], last=card['last'], image=card['image'],
                               bulk_position=task['position'], keep_orders=card['keep_orders'])
                task['phase'] = 'api'
                self._bulk_message(f'正在处理第 {card["first"]}–{card["last"]} 题…')
                if not self._launch_api(request):
                    self._pause_bulk('请求未能启动，请查看底部状态。')
                return
            total = len(plan['cards'])
            skipped = task['skipped']
            self.bulk_task = None
            self.bulk_progress.setRange(0, total)
            self.bulk_progress.setValue(total)
            self.bulk_label.setText(f'批量完成 · {total} 组 · 跳过已有答案 {skipped} 组')
            self._status('本份作业的全部题目已处理完成。')
            self._update_buttons()
        else:
            task['export_dir'], task['render_dir'] = self.export_dir, self.render_dir
            self._bulk_message('正在检查已保存答案和图片顺序…')
            self._start_job({'kind': 'batch_plan'}, 'batch_homework.py',
                            [self.export_dir, '--manifest', self.render_dir/'manifest.json'])

    def start_api(self):
        if self._bulk_running():
            return
        if self.job:
            if self.job['kind'] == 'api':
                self.job['cancelled'] = True
                self.process.kill()
            return
        item = self.cards.currentItem()
        if self.export_dir is None or item is None:
            return
        try:
            prompt_text = self._selected_prompt_text()
        except ValueError as exc:
            self._status(str(exc), True)
            return
        page = item.data(Qt.ItemDataRole.UserRole+1)
        request = {'export_dir': self.export_dir, 'profile': self.api_combo.currentData(),
                   'first': page['first'], 'last': page['last'],
                   'image': item.data(Qt.ItemDataRole.UserRole),
                   'reasoning_effort': self.reasoning_combo.currentData(),
                   'reasoning_parameter': self.reasoning_parameter, 'prompt_text': prompt_text}
        self._launch_api(request)

    def retry_api(self):
        if self._bulk_running():
            return
        request = self.last_api_request
        if self.job is None and request is not None and self.export_dir == request['export_dir']:
            index = self._request_card_index(request)
            if index < 0:
                self._status('上次请求的题目卡片已不存在，请重新选择卡片并调用 API。', True)
                return
            self.cards.setCurrentRow(index)
            if self.job is not None:
                self._status('正在整理刚才的解答，完成后可重试上次请求。')
                return
            self._launch_api(request)

    def _request_card_index(self, request):
        for index in range(self.cards.count()):
            item = self.cards.item(index)
            page = item.data(Qt.ItemDataRole.UserRole+1)
            if (page['first'] == request['first'] and page['last'] == request['last'] and
                    item.data(Qt.ItemDataRole.UserRole) == request['image']):
                return index
        return -1

    def _launch_api(self, request):
        config_input = None
        try:
            profile = request.get('profile_snapshot')
            if profile is None:
                profiles = load_profiles(self.api_config_path)
                profile = next((p for p in profiles if p['id'] == request['profile']), None)
            if profile is None:
                raise ValueError('请先添加并选择 API 配置')
            profile = normalize_profile(profile, require_ready=True)
            if not Path(request['image']).is_file():
                raise ValueError('题目图片不存在，请重新生成后再发送')
            request = request.copy()
            if 'prompt_text' not in request:
                request['prompt_text'] = self._selected_prompt_text()
            if request.get('profile_snapshot'):
                config_input = self._snapshot_prompt(json.dumps({'version': 1, 'profiles': [profile]}, ensure_ascii=False))
            prompt_input = self._snapshot_prompt(request['prompt_text'])
        except (OSError, ValueError, TypeError) as exc:
            if config_input:
                config_input.unlink(missing_ok=True)
            self._status(f'API 配置无效：{exc}', True)
            return False
        self.last_api_request = request.copy()
        self._stash_prompt()
        self.answer_timer.stop()
        self.save_timer.stop()
        card_key = f'{request["first"]}-{request["last"]}'
        job = {'kind': 'api', 'input': prompt_input, 'config_input': config_input,
               'previous_text': self.card_texts.get(card_key, ''), 'card_key': card_key,
               'first': request['first'], 'last': request['last'],
               'export_dir': request['export_dir'],
               'bulk_position': request.get('bulk_position'), 'keep_orders': request.get('keep_orders', []),
               'text': '', 'result': None, 'error': '', 'cancelled': False}
        self.answer_dirty = False
        args = [request['export_dir'], '--config', config_input or self.api_config_path,
                '--profile', request['profile'], '--first', request['first'], '--last', request['last'],
                '--image', request['image'], '--prompt-file', prompt_input]
        if request['reasoning_effort'] is not None:
            args += ['--reasoning-effort', request['reasoning_effort'],
                     '--reasoning-parameter', request['reasoning_parameter']]
        self._start_job(job, 'api_homework.py', args)
        return True

    def _api_event(self, event):
        if not isinstance(event, dict) or not self.job or self.job['kind'] != 'api':
            return
        kind = event.get('type')
        if kind in {'status', 'error'} and isinstance(event.get('message'), str):
            if kind == 'error':
                self.job['error'] = event['message']
            self._status(event['message'], kind == 'error')
        elif kind == 'reasoning' and isinstance(event.get('count'), int) and event['count'] >= 0:
            self._status(f'模型正在思考 · 已返回约 {event["count"]} 字符的思考过程…')
        elif kind == 'delta' and isinstance(event.get('text'), str):
            self.job['text'] += event['text']
            self._set_card_text(self.job['card_key'], self.job['text'])
            self._status(f'第 {self.last_api_request["first"]}–{self.last_api_request["last"]} 题正在接收 API 解答'
                         f' · 已收到 {len(self.job["text"])} 字符…')
        elif kind == 'result' and isinstance(event.get('text'), str):
            self.job['result'] = event

    def _finish_api(self, job, success):
        result = job.get('result')
        valid = bool(success and result and result.get('valid') is True and not job['cancelled'])
        self.api_protected_range = (job['first'], job['last']) if valid else None
        final_text = result['text'] if result and not job['cancelled'] else job['previous_text']
        self._set_card_text(job['card_key'], final_text)
        if job['cancelled']:
            self._status('API 请求已停止，原输入已恢复，未导入半截解答。')
        elif valid:
            self._status('API 解答已返回，正在按题目整理…')
        else:
            reason = job['error'] or (result.get('message') if result else None) or '请求进程未正常完成'
            self._status(f'API 调用未导入：{reason[:240]}', True)
        bulk = job.get('bulk_position') is not None and self.bulk_task is not None
        if bulk:
            if valid:
                self.bulk_task['pending_result'] = {'text': final_text, 'source': job['export_dir'],
                                                    'keep_orders': job['keep_orders'],
                                                    'position': job['bulk_position']}
            else:
                self._pause_bulk('当前组未完成，保留已保存结果；点击继续重试当前组。')
        self._update_buttons()
        self.save_state()
        if valid:
            started = self._start_merge_text(final_text, job['export_dir'], api_result=True,
                                              exclude_orders=job.get('keep_orders'),
                                              bulk_position=job.get('bulk_position'))
            if bulk and not started:
                self._pause_bulk('无法保存已返回答案；继续时会先重试保存。')
        self._drain_pending_merges()
        if self._bulk_running() and self.job is None:
            QTimer.singleShot(0, self._advance_bulk)
        if self.close_requested and self.job is None:
            self.close_requested = False
            self.close()

    def _set_card_text(self, key, text):
        self.card_texts[key] = text
        if self.current_card_key == key:
            self.loading = True
            self.prompt.setPlainText(text)
            cursor = self.prompt.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.prompt.setTextCursor(cursor)
            self.loading = False

    @staticmethod
    def _card_key(item):
        page = item.data(Qt.ItemDataRole.UserRole+1)
        return f'{page["first"]}-{page["last"]}'

    def _stash_prompt(self):
        if self.current_card_key is not None:
            self.card_texts[self.current_card_key] = self.prompt.toPlainText()
        else:
            self.unassigned_text = self.prompt.toPlainText()

    def _card_changed(self, item=None, previous=None):
        key = self._card_key(item) if item else None
        if key != self.current_card_key:
            if not self.loading:
                self._stash_prompt()
                if self.answer_dirty and self.current_card_key is not None:
                    self.start_merge()
            if key is None:
                text = self.prompt.toPlainText()
                self.unassigned_text = text
            else:
                text = self.card_texts.get(key, self.unassigned_text if not self.card_texts else '')
                self.unassigned_text = ''
            previous_loading = self.loading
            self.loading = True
            self.prompt.setPlainText(text)
            self.loading = previous_loading
            self.current_card_key = key
            if not self.loading:
                self.answer_timer.stop()
                self.answer_dirty = False
                self._queue_save()
        page = item.data(Qt.ItemDataRole.UserRole+1) if item else None
        self.batch_label.setText(f'提示词对应：第 {page["first"]}–{page["last"]} 题'
                                 if page else '请选择图片卡片后复制提示词')
        self._update_buttons()

    def _show_answer_count(self):
        total = self.report['question_count'] if self.report else 0
        self.answer_count_label.setText(f'已导入 {len(self.answered)} / {total} 题')
        for index in range(self.cards.count()):
            item = self.cards.item(index)
            page = item.data(Qt.ItemDataRole.UserRole+1)
            done = sum(order in self.answered for order in range(page['first'], page['last']+1))
            extra = '单题超高' if page.get('oversize') else f'{page["question_count"]} 题'
            item.setText(f'第 {page["first"]}–{page["last"]} 题\n{extra} · {page["height"]} px'
                         f'\n解答 {done}/{page["question_count"]}')

    def copy_answer_prompt(self):
        item = self.cards.currentItem()
        if self.job or self.export_dir is None or item is None:
            return
        page = item.data(Qt.ItemDataRole.UserRole+1)
        try:
            prompt_input = self._snapshot_prompt(self._selected_prompt_text())
        except (OSError, ValueError) as exc:
            self._status(f'准备提示词失败：{exc}', True)
            return
        self._start_job({'kind': 'prompt', 'input': prompt_input}, 'answers_homework.py',
                        ['prompt', self.export_dir, '--first', page['first'], '--last', page['last'],
                         '--prompt-file', prompt_input, '--json'])

    def _answer_changed(self):
        if self.loading:
            return
        # The CLI owns parsing; skip ordinary notes/empty edits in the UI.
        self.answer_dirty = bool(self.export_dir and '【解答 ' in self.prompt.toPlainText())
        self.answer_timer.stop()
        if self.answer_dirty:
            self.answer_timer.start()
        elif self.export_dir and self.prompt.toPlainText().strip():
            self.pending_merges.pop(self.current_card_key, None)
            self._status('尚未发现解答标记。请先复制提示词，让 AI 按指定格式输出，再粘贴完整 Markdown。')
        else:
            self.pending_merges.pop(self.current_card_key, None)

    def start_merge(self):
        if not self.answer_dirty or self.export_dir is None:
            return
        self.answer_timer.stop()
        text = self.prompt.toPlainText()
        self.answer_dirty = False
        self.pending_merges.pop(self.current_card_key, None)
        if self.job is not None:
            self.pending_merges[self.current_card_key] = (self.export_dir, text)
            return
        self._start_merge_text(text, self.export_dir, exclude_range=self.api_protected_range)

    def _start_merge_text(self, text, source, exclude_range=None, api_result=False,
                          exclude_orders=None, bulk_position=None):
        input_path = PROJECT/'data'/('answer-input-'+uuid.uuid4().hex+'.md')
        try:
            input_path.parent.mkdir(parents=True, exist_ok=True)
            input_path.write_text(text, encoding='utf-8')
        except OSError as exc:
            self._status(f'保存回复失败：{exc}', True)
            return False
        arguments = ['merge', source, '--input', input_path]
        if exclude_range is not None:
            arguments += ['--exclude-range', *exclude_range]
        if exclude_orders:
            arguments += ['--exclude-orders', *exclude_orders]
        self._start_job({'kind': 'merge', 'input': input_path, 'api_result': api_result,
                         'bulk_position': bulk_position},
                        'answers_homework.py', arguments)
        return True

    def _drain_pending_merges(self):
        if self.job is not None:
            return
        if self.answer_dirty:
            self.start_merge()
            if self.job is not None:
                return
        while self.pending_merges:
            _, (source, text) = self.pending_merges.popitem()
            if source == self.export_dir:
                self._start_merge_text(text, source, exclude_range=self.api_protected_range)
                return
        self.api_protected_range = None

    def open_study(self):
        if self.study_path and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.study_path))):
            self._status('无法打开整理文档，请检查 Markdown 默认打开程序。', True)

    def _new_run(self, parent):
        return parent/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6])

    def start_convert(self):
        if self.source is None or self.job:
            return
        output_root = self.directory/'exports'
        if (output_root/self.source.stem).exists():
            output_root = self._new_run(output_root/'ui_runs')
        job = {'kind': 'convert', 'target': output_root/self.source.stem}
        self._start_job(job, 'extract_homework.py',
                        [self.source, '-o', output_root, '--ocr-model', self.ocr_model_combo.currentData(),
                         '--progress-json'])

    def start_render(self):
        if self.export_dir is None or self.job:
            return
        output_root = self._new_run(self.export_dir/'render_runs')
        args = [self.export_dir, '-o', output_root, '--width', self.width_spin.value(), '--progress-json']
        if self.height_mode.isChecked():
            args += ['--max-height', self.height_spin.value()]
        else:
            args += ['-n', self.n_spin.value()]
        self._start_job({'kind': 'render', 'target': output_root/self.export_dir.name},
                        'render_homework.py', args)

    def _start_job(self, job, script, arguments):
        self.save_state()
        self.job, self.log = job, b''
        self.output_pending = b''
        program, args = cli_command(script, arguments)
        self.process.setWorkingDirectory(str(self.directory))
        self.process.setProgram(program)
        self.process.setArguments(args)
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.show()
        self._update_buttons()
        self._status({'convert': '正在转换为 Markdown…', 'render': '正在排版并生成图片…',
                      'prompt': '正在生成当前卡片的提示词…', 'merge': '正在按题目整理解答…',
                      'batch_plan': '正在检查全部题目和已保存答案…',
                      'api': '正在调用所选 API 解答当前卡片…'}[job['kind']])
        self.process.start()

    def _read_output(self):
        self._consume_output(bytes(self.process.readAllStandardOutput()))

    def _consume_output(self, chunk):
        self.log += chunk
        if self.job is None or self.job['kind'] not in {'convert', 'render', 'api'}:
            return
        self.output_pending += chunk
        while b'\n' in self.output_pending:
            line, self.output_pending = self.output_pending.split(b'\n', 1)
            prefix = API_PREFIX if self.job['kind'] == 'api' else PROGRESS_PREFIX
            if not line.startswith(prefix.encode('ascii')):
                continue
            try:
                event = json.loads(line[len(prefix):].decode('utf-8'))
                if self.job['kind'] == 'api':
                    self._api_event(event)
                else:
                    self._update_progress(event)
            except (UnicodeError, ValueError, TypeError, KeyError):
                # Ordinary logs and malformed events must not interrupt a job.
                continue

    def _update_progress(self, event):
        if not isinstance(event, dict) or not isinstance(event.get('message'), str):
            return
        current, total = event.get('current', 0), event.get('total', 0)
        if not isinstance(current, int) or not isinstance(total, int) or current < 0 or total < 0:
            return
        if total:
            self.progress.setRange(0, total)
            self.progress.setValue(min(current, total))
            self.progress.setFormat('%v / %m · %p%')
            self.progress.setTextVisible(True)
        else:
            self.progress.setRange(0, 0)
            self.progress.setTextVisible(False)
        self._status(event['message'])

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.log += self.process.errorString().encode('utf-8')
            self._process_finished(-1, QProcess.ExitStatus.CrashExit)

    def _process_finished(self, code, exit_status):
        if self.job is None:
            return
        self._read_output()
        job, self.job = self.job, None
        for temporary in (job.get('input'), job.get('config_input')):
            if temporary:
                temporary.unlink(missing_ok=True)
        self.progress.hide()
        success = code == 0 and exit_status == QProcess.ExitStatus.NormalExit
        if job['kind'] == 'api':
            self._finish_api(job, success)
            return
        if success:
            if job['kind'] == 'convert':
                self._load_export(job['target'], expected_source=self.source.name)
                success = self.export_dir == job['target']
                if success:
                    self.render_dir = None
                    self.cards.clear()
                    self.cards_label.setText('题目图片 · 尚未生成')
                    self._status(f'Markdown 转换完成，共 {self.report["question_count"]} 题。')
            elif job['kind'] == 'render':
                self._load_cards(job['target'])
                success = self.render_dir == job['target']
                if success:
                    self._status(f'已生成 {self.cards.count()} 张图片，点击缩略图即可复制。')
            elif job['kind'] == 'batch_plan':
                try:
                    plan = json.loads(self.log.decode('utf-8'))
                    if not isinstance(plan.get('cards'), list) or not plan['cards']:
                        raise ValueError('未找到有效的图片调用计划')
                    if self.bulk_task:
                        self.bulk_task.update(plan=plan, phase='dispatch')
                except (ValueError, KeyError, TypeError) as exc:
                    self.log += f'\n失败：{exc}'.encode('utf-8')
                    success = False
            else:
                try:
                    result = json.loads(self.log.decode('utf-8'))
                    if job['kind'] == 'prompt':
                        QApplication.clipboard().setText(result['prompt'])
                        self._status('提示词已复制。粘贴到聊天窗口后，再点击对应卡片的缩略图复制题图。')
                    else:
                        self.answered = set(result['answered'])
                        self.study_path = Path(result['document']) if result['document'] else None
                        self._show_answer_count()
                        self.review_panel.set_export(self.export_dir, refresh=True)
                        self.correction_panel.set_export(self.export_dir, refresh=True)
                        if result['status'] == 'merged':
                            self._status(f'本次识别 {len(result["updated"])} 道解答；'
                                         f'累计 {result["answered_count"]}/{result["question_count"]} 题，整理文档已更新。')
                        elif result['status'] == 'protected':
                            self._status('这份粘贴内容仅包含 API 已解答的题目；已保留 API 结果。')
                        else:
                            self._status('未识别到完整的解答标记。已有解答已保留，请使用复制的提示词。')
                except (ValueError, KeyError, TypeError) as exc:
                    self.log += f'\n失败：无法读取 CLI 结果：{exc}'.encode('utf-8')
                    success = False
        if not success:
            action = {'convert': '转换', 'render': '生成图片', 'prompt': '复制提示词',
                      'merge': '整理解答', 'batch_plan': '检查批量任务'}[job['kind']]
            lines = self.log.decode('utf-8', errors='replace').strip().splitlines()
            reason = next((line.split('失败：', 1)[-1] for line in reversed(lines) if '失败：' in line),
                          lines[-1] if lines else '未生成预期结果')
            self._status(f'{action}失败：{reason[:160]}。可查看运行日志。', True)
        if self.bulk_task:
            if success and job['kind'] in {'convert', 'render'}:
                self.bulk_task.update(export_dir=self.export_dir, render_dir=self.render_dir)
            if success and job['kind'] == 'merge' and job.get('bulk_position') is not None:
                self.bulk_task.update(pending_result=None, phase='prepare', position=job['bulk_position']+1)
                self._bulk_message('当前组已保存，准备下一组…' if self._bulk_running() else '已暂停 · 当前组已保存。')
            elif success and job['kind'] == 'merge':
                self.bulk_task['phase'] = 'prepare'
            if not success:
                self._pause_bulk('当前步骤失败，请查看底部状态；继续时从未完成处重试。')
        if job.get('api_result') and not success:
            self.api_protected_range = None
        self._update_buttons()
        self.save_state()
        # An edit may arrive while a CLI is running. Always process the latest
        # text after it finishes, rather than losing that queued paste.
        self._drain_pending_merges()
        if self._bulk_running() and self.job is None:
            QTimer.singleShot(0, self._advance_bulk)
        if self.close_requested and self.job is None:
            self.close_requested = False
            self.close()

    def copy_card(self, item):
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        try:
            image = QImage(str(path))
            if image.isNull():
                raise ValueError('图片无法读取')
            # Let Qt create/own the native image MIME object. Python-created
            # QMimeData image objects can crash PySide6 at interpreter shutdown.
            QApplication.clipboard().setImage(image)
            self._status(f'已复制{item.text().splitlines()[0]}的完整图片，可粘贴到聊天窗口。')
        except (OSError, ValueError) as exc:
            self._status(f'复制图片失败：{exc}', True)

    def open_output(self, folder):
        if self.export_dir is None:
            return
        target = self.export_dir if folder else self.export_dir/'questions.md'
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            self._status('无法打开，请检查系统默认程序。', True)

    def show_log(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('运行日志')
        dialog.resize(780, 480)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit(self.log.decode('utf-8', errors='replace') or '尚无运行日志。')
        text.setReadOnly(True)
        layout.addWidget(text)
        close = QPushButton('关闭')
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def _status(self, text, error=False):
        self.status_label.setText(text)
        self.status_label.setStyleSheet('color: #aa3434;' if error else 'color: #51677c;')

    def _queue_save(self, *args):
        if not self.loading:
            self.save_timer.start()

    def save_state(self):
        if self.loading:
            return
        self.state['render_settings'] = self._render_settings()
        self.state['ocr_model'] = self.ocr_model_combo.currentData()
        self.state['selected_api'] = self.api_combo.currentData()
        self.state['selected_prompt_template'] = self.prompt_template_combo.currentData()
        if self.source is not None:
            self._stash_prompt()
            self.state['selected_source'] = str(self.source)
            previous = self.state.get('files', {}).get(str(self.source), {})
            self.state.setdefault('files', {})[str(self.source)] = {
                'source_file': self.report['source_file'] if self.report else previous.get('source_file', self.source.name),
                'export_dir': str(self.export_dir) if self.export_dir else None,
                'render_dir': str(self.render_dir) if self.render_dir else None,
                'text': self.prompt.toPlainText(),
                'card_texts': self.card_texts,
                'selected_card': self.current_card_key,
            }
        temporary = self.state_path.with_name('.gui-state-'+uuid.uuid4().hex+'.tmp')
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            temporary.replace(self.state_path)
        except OSError as exc:
            self._status(f'保存设置失败：{exc}', True)
        finally:
            if temporary.exists():
                temporary.unlink()

    def closeEvent(self, event):
        if self._bulk_running():
            self._pause_bulk('窗口即将关闭，完成当前步骤后停止；下次可跳过已完成的组。')
        self.answer_timer.stop()
        if self.answer_dirty:
            self.start_merge()
        if self.job is None and self.pending_merges:
            self._drain_pending_merges()
        if self.job:
            self.close_requested = True
            self._status('任务正在运行，完成后即可关闭窗口。')
            event.ignore()
            return
        self.save_timer.stop()
        self.save_state()
        if (self.reasoning_process is not None and
                self.reasoning_process.state() != QProcess.ProcessState.NotRunning):
            self.reasoning_process.kill()
            self.reasoning_process.waitForFinished(1000)
        self.review_panel.shutdown()
        self.correction_panel.shutdown()
        event.accept()


def main():
    parser = argparse.ArgumentParser(description='作业 Markdown 与图片转换的 Qt 界面。')
    parser.add_argument('directory', nargs='?', type=Path, default=Path.cwd(), help='要扫描的文件夹')
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setStyle('Fusion')
    app.setPalette(light_palette())
    window = HomeworkWindow(args.directory)
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
