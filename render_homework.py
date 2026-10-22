"""Render exported questions into local PNGs, with n complete questions per image."""
from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
import re
import shutil
import sys
import uuid
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.dont_write_bytecode = True

import markdown
from PIL import Image
from cli_progress import print_progress, update_progress


CSS = """
* { box-sizing: border-box; }
html { scrollbar-width: none; }
::-webkit-scrollbar { display: none; }
body { margin: 0; color: #202733; background: white;
       font: 26px/1.65 'Microsoft YaHei', 'Noto Sans SC', sans-serif; }
main { padding: 36px 44px 44px; }
header { padding-bottom: 20px; border-bottom: 3px solid #2d5f91; }
header h1 { margin: 0; font-size: 31px; font-weight: 700; }
header p { margin: 6px 0 0; font-size: 19px; color: #586576; }
.assignment { margin: 28px 0 12px; font-size: 23px; color: #315573; }
article { padding: 22px 0 26px; border-bottom: 1px solid #d7dde4; }
article:last-child { border-bottom: 0; padding-bottom: 0; }
h3 { margin: 0 0 12px; font-size: 25px; color: #1b456e; }
p { margin: 8px 0; }
.stem, .option-text { overflow-wrap: anywhere; }
.option { display: flex; gap: 12px; margin-top: 6px; }
.label { flex: 0 0 38px; font-weight: 600; }
.option-text { min-width: 0; flex: 1; }
.option-text > p:first-child { margin-top: 0; }
.option-text > p:last-child { margin-bottom: 0; }
.blanks { font-size: 19px; color: #586576; margin-top: 10px; }
.notice { font-size: 19px; color: #845e1b; margin: 14px 0 0; padding-left: 12px;
          border-left: 3px solid #d2ad60; }
sub, sup { font-size: 72%; line-height: 0; }
img { max-width: 100%; height: auto; vertical-align: middle; }
img.diagram { display: block; margin: 14px auto; }
table { border-collapse: collapse; max-width: 100%; }
td, th { border: 1px solid #cbd3db; padding: 6px 12px; }
pre { white-space: pre-wrap; }
"""


class LocalContent(HTMLParser):
    """Keep Markdown formatting and embed only images from the export directory."""
    TAGS = {'p', 'br', 'strong', 'em', 'b', 'i', 'sub', 'sup', 'code', 'pre',
            'ul', 'ol', 'li', 'blockquote', 'hr', 'table', 'thead', 'tbody',
            'tr', 'td', 'th', 'del'}

    def __init__(self, directory, image_cache):
        super().__init__(convert_charrefs=True)
        self.directory = directory.resolve()
        self.image_cache = image_cache
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.TAGS:
            self.parts.append(f'<{tag}>')
        elif tag == 'img':
            attrs = dict(attrs)
            src = urlsplit(attrs.get('src', ''))
            if src.scheme or src.netloc or not src.path:
                raise ValueError('题图必须是导出目录中的本地图片。')
            path = (self.directory / unquote(src.path)).resolve()
            if not path.is_relative_to(self.directory):
                raise ValueError(f'题图路径超出导出目录：{attrs.get("src")}')
            if path not in self.image_cache:
                mime = mimetypes.guess_type(path.name)[0]
                if not mime or not mime.startswith('image/'):
                    raise ValueError(f'无法渲染题图格式：{path.name}')
                blob = path.read_bytes()
                dimensions = None
                if mime != 'image/svg+xml':
                    with Image.open(path) as image:
                        dimensions = image.size
                self.image_cache[path] = (f'data:{mime};base64,' +
                                          base64.b64encode(blob).decode('ascii'), dimensions)
            uri, dimensions = self.image_cache[path]
            # Small, single-line formula images remain inline with the text.
            inline = dimensions and dimensions[1] <= 85 and dimensions[0] <= 420
            scale = max(1.3, min(2.2, 32/dimensions[1])) if inline else 1.3
            width = f' width="{round(dimensions[0] * scale)}"' if dimensions else ''
            kind = 'formula' if inline else 'diagram'
            self.parts.append(f'<img class="{kind}" src="{uri}"{width} '
                              f'alt="{html.escape(attrs.get("alt", "题图"), quote=True)}">')
        else:
            self.parts.append(html.escape(self.get_starttag_text()))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in self.TAGS and tag not in {'br', 'hr'}:
            self.parts.append(f'</{tag}>')
        elif tag not in {'img', 'br', 'hr'}:
            self.parts.append(html.escape(f'</{tag}>'))

    def handle_data(self, data):
        self.parts.append(html.escape(data))


def rich_text(text, directory, image_cache):
    parser = LocalContent(directory, image_cache)
    converter = markdown.Markdown(extensions=['nl2br', 'tables'])
    # Question blanks are literal underscores, not Markdown emphasis. Keep
    # asterisk formatting and image filenames intact, and protect blank-only lines.
    converter.inlinePatterns.deregister('em_strong2')
    text = re.sub(r'(?m)^(\s*)(_{3,})(\s*)$',
                  lambda match: match[1]+match[2].replace('_', '\\_')+match[3], text)
    parser.feed(converter.convert(text))
    parser.close()
    return ''.join(parser.parts)


def group_questions(report, n):
    if n < 1:
        raise ValueError('每张图片的题数必须大于 0。')
    questions = []
    for index, assignment in enumerate(report['assignments'], 1):
        for question in assignment['questions']:
            questions.append({'order': len(questions)+1, 'assignment': index,
                              'title': assignment['title'], 'question': question})
    if not questions:
        raise ValueError('导出文件中没有题目。')
    return [questions[start:start+n] for start in range(0, len(questions), n)]


def batch_html(report, group, directory, image_cache):
    escape = html.escape
    first, last = group[0]['order'], group[-1]['order']
    total = sum(len(a['questions']) for a in report['assignments'])
    title = Path(report['source_file']).stem
    parts = [f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
             f'<title>{escape(title)} · {first}–{last}</title><style>{CSS}</style></head>'
             f'<body><main><header><h1>{escape(title)} · 第 {first}–{last} 题</h1>'
             f'<p>本张 {len(group)} 题 / 共 {total} 题 · 保留原作业题号</p></header>']
    previous = None
    for entry in group:
        q = entry['question']
        if entry['assignment'] != previous:
            parts.append(f'<h2 class="assignment">作业 {entry["assignment"]}：'
                         f'{escape(entry["title"])}</h2>')
            previous = entry['assignment']
        parts.append(f'<article data-order="{entry["order"]}"><h3>'
                     f'第 {escape(str(q["number"]))} 题 · {escape(q["type"])}</h3>'
                     f'<div class="stem">{rich_text(q["stem"], directory, image_cache)}</div>')
        for option in q['options']:
            parts.append(f'<div class="option"><span class="label">'
                         f'{escape(option["label"])}.</span><div class="option-text">'
                         f'{rich_text(option["text"], directory, image_cache)}</div></div>')
        if q.get('blank_count'):
            parts.append(f'<p class="blanks">填空数量：{int(q["blank_count"])}</p>')
        context = f'{entry["title"]} 第 {q["number"]} 题：'
        for warning in report.get('warnings', []):
            if warning.startswith(context):
                parts.append(f'<p class="notice">导出提示：{escape(warning[len(context):])}</p>')
        parts.append('</article>')
    parts.append('</main></body></html>')
    return ''.join(parts)


class BrowserRenderer:
    def __init__(self, width=1280, progress=None):
        if width < 480:
            raise ValueError('图片宽度不能小于 480 像素。')
        self.width = width
        self.progress = progress
        self.playwright = self.browser = self.context = self.page = None

    def __enter__(self):
        update_progress(self.progress, 'browser', '正在启动图片渲染浏览器…')
        from playwright.sync_api import Error, sync_playwright
        self.playwright = sync_playwright().start()
        try:
            failures = []
            for channel in ('msedge', 'chrome', None):
                try:
                    self.browser = self.playwright.chromium.launch(headless=True, channel=channel)
                    break
                except Error as exc:
                    failures.append(str(exc))
            if self.browser is None:
                raise OSError('无法启动渲染浏览器。需要本机 Edge/Chrome，或运行 '
                              'python -m playwright install chromium。\n' + failures[0])
            self.context = self.browser.new_context(viewport={'width': self.width, 'height': 900},
                                                    device_scale_factor=1)
            # All CSS and images are embedded. Nothing needs a network request.
            self.context.route('**/*', lambda route: route.abort())
            self.page = self.context.new_page()
            self.page.set_default_timeout(30000)
            return self
        except BaseException:
            self.close()
            raise

    def measure(self, content):
        from playwright.sync_api import Error
        try:
            self.page.set_content(content, wait_until='load')
            self.page.evaluate('''async () => {
                await document.fonts.ready;
                await Promise.all(Array.from(document.images, img => img.decode()));
            }''')
            bounds = self.page.evaluate('''() => {
                const main = document.querySelector('main');
                return {height: Math.ceil(main.getBoundingClientRect().height),
                        width: document.documentElement.scrollWidth};
            }''')
            if bounds['width'] > self.width:
                raise ValueError('题目内容超出图片宽度，请增加 --width。')
            return bounds['height']
        except Error as exc:
            raise OSError(f'浏览器渲染失败：{exc}') from exc

    def capture(self, content, path):
        from playwright.sync_api import Error
        try:
            height = self.measure(content)
            # Element screenshot fits the actual document, without viewport whitespace.
            self.page.locator('main').screenshot(path=str(path), animations='disabled', scale='css')
            with Image.open(path) as image:
                if image.width != self.width or image.height != height:
                    raise ValueError('渲染图片尺寸不完整，请减小每张题数后重试。')
                return image.size
        except Error as exc:
            raise OSError(f'浏览器渲染失败：{exc}') from exc

    def close(self):
        if self.browser is not None:
            update_progress(self.progress, 'browser_close', '图片已处理，正在关闭渲染浏览器…')
        try:
            if self.browser is not None:
                self.browser.close()
        finally:
            self.browser = self.page = self.context = None
            if self.playwright is not None:
                self.playwright.stop()
                self.playwright = None

    def __exit__(self, *args):
        self.close()


def group_by_height(report, directory, image_cache, renderer, max_height, progress=None):
    """Greedily fit whole questions using their actual browser layout height."""
    questions = [group[0] for group in group_questions(report, 1)]
    groups, current = [], []
    for index, entry in enumerate(questions, 1):
        update_progress(progress, 'measure', f'正在测量第 {index}/{len(questions)} 题的排版高度…',
                        index-1, len(questions))
        candidate = current + [entry]
        height = renderer.measure(batch_html(report, candidate, directory, image_cache))
        if height <= max_height:
            current = candidate
            update_progress(progress, 'measure', f'已测量 {index}/{len(questions)} 题', index, len(questions))
            continue
        if current:
            groups.append(current)
            candidate = [entry]
            height = renderer.measure(batch_html(report, candidate, directory, image_cache))
        if height > max_height:
            groups.append(candidate)  # A single tall question is kept whole.
            current = []
        else:
            current = candidate
        update_progress(progress, 'measure', f'已测量 {index}/{len(questions)} 题', index, len(questions))
    if current:
        groups.append(current)
    return groups


def render(source: Path, output=None, n=5, width=1280, renderer=None, max_height=None, progress=None):
    source = Path(source)
    source = source/'questions.json' if source.is_dir() else source
    output = Path(output) if output is not None else source.parent/'rendered'
    if n < 1 or width < 480:
        raise ValueError('题数必须大于 0，图片宽度不能小于 480 像素。')
    if max_height is not None and max_height < 1:
        raise ValueError('最大图片高度必须大于 0。')
    if output.exists():
        raise ValueError(f'渲染目录已存在：{output}。请使用 -o 指定新的目录，避免覆盖。')
    update_progress(progress, 'read', '正在读取题目数据，准备图片排版…')
    report = json.loads(source.read_text(encoding='utf-8'))
    groups = group_questions(report, n)
    if renderer is None:
        with BrowserRenderer(width, progress=progress) as owned:
            return render(source, output, n, width, owned, max_height, progress)
    if renderer.width != width:
        raise ValueError('共享渲染器的宽度与请求宽度不同。')
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent/('.render-' + uuid.uuid4().hex)
    staging.mkdir()  # Preserve normal inherited Windows permissions.
    image_cache, pages = {}, []
    try:
        if max_height is not None:
            groups = group_by_height(report, source.parent, image_cache, renderer, max_height, progress)
        for index, group in enumerate(groups, 1):
            first, last = group[0]['order'], group[-1]['order']
            update_progress(progress, 'render',
                            f'正在生成第 {index}/{len(groups)} 张图片（第 {first}–{last} 题）…',
                            index-1, len(groups))
            name = f'batch_{index:03d}_q{first:03d}-{last:03d}.png'
            content = batch_html(report, group, source.parent, image_cache)
            size = renderer.capture(content, staging/name)
            if max_height is not None and size[1] > max_height and len(group) > 1:
                raise ValueError('图片实际高度超过分组限制，请重试。')
            pages.append({'image': name, 'first': first, 'last': last,
                          'question_count': len(group), 'width': size[0], 'height': size[1],
                          'oversize': max_height is not None and size[1] > max_height,
                          'questions': [{'order': e['order'], 'assignment': e['assignment'],
                                         'title': e['title'], 'id': e['question']['id'],
                                         'number': e['question']['number']} for e in group]})
            update_progress(progress, 'render', f'已生成 {index}/{len(groups)} 张图片', index, len(groups))
        manifest = {'source_file': report['source_file'],
                    'grouping': 'height' if max_height is not None else 'count',
                    'questions_per_image': n if max_height is None else None,
                    'max_height': max_height,
                    'question_count': sum(len(g) for g in groups), 'image_count': len(pages),
                    'warnings': report.get('warnings', []), 'pages': pages}
        update_progress(progress, 'write', '正在保存图片和分组清单…')
        (staging/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n',
                                            encoding='utf-8')
        staging.rename(output)
        update_progress(progress, 'saved', f'已保存 {len(pages)} 张图片', len(pages), len(pages))
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def find_exports(paths):
    sources = []
    for path in paths:
        if path.is_file():
            sources.append(path)
        elif (path/'questions.json').is_file():
            sources.append(path/'questions.json')
        elif path.is_dir():
            sources.extend(sorted(path.glob('*/questions.json')))
        else:
            raise ValueError(f'输入路径不存在：{path}')
    return list(dict.fromkeys(path.resolve() for path in sources))


def main():
    parser = argparse.ArgumentParser(description='每 n 道导出题目渲染并拼成一张 PNG 长图。')
    parser.add_argument('sources', nargs='+', type=Path,
                        help='questions.json、单份导出目录，或包含多份导出的目录')
    parser.add_argument('-n', '--questions-per-image', type=int, default=5, help='每张图片题数（默认 5）')
    parser.add_argument('--width', type=int, default=1280, help='图片宽度，单位像素（默认 1280）')
    parser.add_argument('--max-height', type=int, help='按最大图片高度分组，覆盖 -n；超高单题单独输出')
    parser.add_argument('-o', '--output', type=Path, help='可选输出根目录；默认保存到各导出目录/rendered')
    parser.add_argument('--progress-json', action='store_true', help='实时输出供界面读取的进度事件')
    args = parser.parse_args()
    if args.questions_per_image < 1 or args.width < 480:
        parser.error('题数必须大于 0，宽度不能小于 480 像素。')
    if args.max_height is not None and args.max_height < 1:
        parser.error('最大图片高度必须大于 0。')
    try:
        sources = find_exports(args.sources)
        if not sources:
            parser.error('没有找到 questions.json，请先运行提取程序。')
        failures = 0
        progress = print_progress if args.progress_json else None
        with BrowserRenderer(args.width, progress=progress) as renderer:
            for source in sources:
                output = (args.output/source.parent.name if args.output is not None
                          else source.parent/'rendered')
                try:
                    manifest = render(source, output, args.questions_per_image, args.width, renderer,
                                      args.max_height, progress)
                    print(f'{source.parent.name}：{manifest["question_count"]} 题 → '
                          f'{manifest["image_count"]} 张 PNG，保存到 {output}')
                except (OSError, ValueError, KeyError) as exc:
                    failures += 1
                    print(f'{source.parent.name}：渲染失败：{exc}', file=sys.stderr)
        return 1 if failures else 0
    except (OSError, ValueError) as exc:
        print(f'渲染失败：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
