"""Offline Qt reader powered by vendored markdown-it, texmath and KaTeX."""
from __future__ import annotations

import base64
import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import QWebEngineContextMenuRequest, QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMenu

ASSETS = Path(__file__).resolve().parent/'data/preview'
IMAGE = re.compile(r'(!\[[^\n]*?\]\()([^\n)]+)(\))')


def local_images(text, directory, strict=False):
    directory = Path(directory).resolve()
    def replace(match):
        src = match[2].strip().strip('<>')
        url = urlsplit(src)
        path = (directory/unquote(url.path)).resolve()
        mime = mimetypes.guess_type(path.name)[0]
        if url.scheme or url.netloc or not path.is_relative_to(directory) or mime not in {
                'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/bmp'}:
            if strict:
                raise ValueError('题图必须是导出文件夹内的本地图片')
            return '[图片不可用]'
        try:
            uri = f'data:{mime};base64,'+base64.b64encode(path.read_bytes()).decode('ascii')
            return match[1]+uri+match[3]
        except OSError:
            if strict:
                raise ValueError(f'题图未保存：{path.name}')
            return '[图片未保存：'+path.name+']'
    return IMAGE.sub(replace, text)


class AssetRequests(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info):
        url = info.requestUrl()
        allowed = url.scheme() == 'data'
        if url.isLocalFile():
            path = Path(url.toLocalFile()).resolve()
            allowed = path.is_relative_to(ASSETS) and path.suffix in {'.html', '.js', '.css', '.woff2'}
        if not allowed:
            info.block(True)


class ReaderPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        return url.isLocalFile() and Path(url.toLocalFile()).resolve() == ASSETS/'index.html'


class MarkdownPreview(QWebEngineView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.profile = QWebEngineProfile(self)  # Off the record: no persistent browser cache.
        self.interceptor = AssetRequests(self.profile)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.setPage(ReaderPage(self.profile, self))
        self.ready = False
        self.load_error = ''
        self.sections = [{'text': '请选择一道题。'}]
        self.scroll_bottom = False
        self.loadFinished.connect(self._loaded)
        self.page().loadingChanged.connect(lambda info: setattr(self, 'load_error', info.errorString()))
        self.setMinimumHeight(140)
        self.load(QUrl.fromLocalFile(str(ASSETS/'index.html')))

    def _loaded(self, success):
        self.ready = success
        if success:
            self._render()

    def set_sections(self, sections, directory=None, scroll_bottom=False):
        self.sections = [dict(section, text=local_images(section['text'], directory)
                              if directory else section['text']) for section in sections]
        self.scroll_bottom = scroll_bottom
        if self.ready:
            self._render()

    def _render(self):
        payload = json.dumps(self.sections, ensure_ascii=True)
        self.page().runJavaScript(f'renderSections({payload}, {str(self.scroll_bottom).lower()})')

    def contextMenuEvent(self, event):
        request = self.lastContextMenuRequest()
        menu = QMenu(self)
        if request and request.mediaType() == QWebEngineContextMenuRequest.MediaType.MediaTypeImage:
            copy_image = menu.addAction('复制题图')
            copy_image.triggered.connect(lambda: self.page().triggerAction(QWebEnginePage.WebAction.CopyImageToClipboard))
        if request and request.selectedText():
            copy_text = menu.addAction('复制选中文字')
            copy_text.triggered.connect(lambda: self.page().triggerAction(QWebEnginePage.WebAction.Copy))
        select_all = menu.addAction('全选')
        select_all.triggered.connect(lambda: self.page().triggerAction(QWebEnginePage.WebAction.SelectAll))
        menu.exec(event.globalPos())
