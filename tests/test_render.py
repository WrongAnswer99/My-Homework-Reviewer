import json
import os
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from extract_homework import extract
from render_homework import BrowserRenderer, render, rich_text


ROOT = Path(__file__).resolve().parents[1]


def example_export(folder, broken=False):
    (folder/'images').mkdir(parents=True)
    # Oversized diagram tests resizing, embedding, and actual image pixels.
    Image.new('RGB', (1600, 180), '#d32929').save(folder/'images/diagram.png')
    report = {'source_file': '渲染样例.mhtml', 'warnings': [], 'assignments': [
        {'title': '第一份作业', 'questions': [
            {'id': 'q1', 'number': '1', 'type': '单选题',
             'stem': '**加粗题干**：P<sub>CM</sub> 与 x<sup>2</sup>。\n'
                     '较长的中文题干需要自动换行。'*12,
             'options': [{'label': 'A', 'text': '选项里有图 ![题图](images/diagram.png)'}]},
            {'id': 'q2', 'number': '2', 'type': '填空题', 'blank_count': 2,
             'stem': '填空____。' + ('![题图](images/missing.png)' if broken else ''), 'options': []}]},
        {'title': '第二份作业', 'questions': [
            {'id': 'q3', 'number': '1', 'type': '判断题', 'stem': '第三道题，原题号重新从 1 开始。',
             'options': [{'label': 'A', 'text': '正确'}, {'label': 'B', 'text': '错误'}]}]}]}
    source = folder/'questions.json'
    source.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
    return source


class RenderInputTests(unittest.TestCase):
    def test_fill_underscores_survive_markdown_without_unintended_emphasis(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            content = rich_text('**加粗**：第一空____；第二空____。\n\n____', Path(temp), {})
            self.assertIn('<strong>加粗</strong>', content)
            self.assertIn('第一空____；第二空____。', content)
            self.assertIn('<p>____</p>', content)
            self.assertNotIn('<em>', content)

    def test_rejects_external_and_parent_image_paths(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            for src in ('https://example.test/photo.png', '../outside.png'):
                with self.subTest(src=src), self.assertRaises(ValueError):
                    rich_text(f'![题图]({src})', Path(temp), {})

    def test_rejects_invalid_group_size_and_existing_output(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            source = example_export(Path(temp))
            with self.assertRaisesRegex(ValueError, '题数必须大于 0'):
                render(source, n=0)
            output = Path(temp)/'rendered'
            output.mkdir()
            note = output/'keep.txt'
            note.write_text('已有文件', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '渲染目录已存在'):
                render(source)
            self.assertEqual(note.read_text(encoding='utf-8'), '已有文件')

    def test_extraction_render_failure_is_atomic(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            source, output = Path(temp)/'test.mhtml', Path(temp)/'result'
            message = EmailMessage()
            message.make_related()
            part = EmailMessage()
            part.set_content('<h3>测试作业</h3><div class="singleQuesId" data="q1">'
                             '<div class="Zy_TItle"><i>1</i><div class="fontLabel">题干</div>'
                             '</div></div>', subtype='html', charset='utf-8')
            message.attach(part)
            source.write_bytes(message.as_bytes())
            with patch('render_homework.render', side_effect=OSError('模拟渲染失败')):
                with self.assertRaisesRegex(OSError, '模拟渲染失败'):
                    extract(source, output, render_every=5)
            self.assertFalse(output.exists())
            self.assertFalse(list(Path(temp).glob('.extract-*')))


@unittest.skipUnless(os.name == 'nt', 'Uses the locally installed Edge browser')
class RenderBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.renderer = BrowserRenderer(960).__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.renderer.close()

    def test_actual_png_grouping_images_and_full_height(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            source = example_export(Path(temp))
            output = Path(temp)/'rendered'
            manifest = render(source, n=2, width=960, renderer=self.renderer)
            self.assertEqual([p['question_count'] for p in manifest['pages']], [2, 1])
            self.assertEqual([q['id'] for p in manifest['pages'] for q in p['questions']],
                             ['q1', 'q2', 'q3'])
            self.assertEqual(manifest['pages'][1]['questions'][0]['number'], '1')
            for page in manifest['pages']:
                with Image.open(output/page['image']) as image:
                    self.assertEqual(image.size, (960, page['height']))
                    self.assertGreater(image.height, 250)
            with Image.open(output/manifest['pages'][0]['image']) as image:
                red = image.convert('RGB').getcolors(image.width*image.height)
                self.assertGreater(sum(count for count, color in red if color == (211, 41, 41)), 20000)
            self.assertEqual(json.loads((output/'manifest.json').read_text(encoding='utf-8')), manifest)
            self.assertFalse(list(Path(temp).glob('.render-*')))

    def test_missing_image_cleans_already_rendered_batches(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            source = example_export(Path(temp), broken=True)
            with self.assertRaises(FileNotFoundError):
                render(source, n=1, width=960, renderer=self.renderer)
            self.assertFalse((Path(temp)/'rendered').exists())
            self.assertFalse(list(Path(temp).glob('.render-*')))

    def test_max_height_packs_whole_questions_and_isolates_oversized_one(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            source = example_export(Path(temp))
            progress = []
            manifest = render(source, n=99, width=960, renderer=self.renderer, max_height=1000,
                              progress=progress.append)
            self.assertEqual([e['current'] for e in progress if e['stage'] == 'measure'
                              and e['message'].startswith('已测量')], [1, 2, 3])
            self.assertEqual([e['current'] for e in progress if e['stage'] == 'render'
                              and e['message'].startswith('已生成')], list(range(1, len(manifest['pages'])+1)))
            self.assertEqual(progress[-1]['stage'], 'saved')
            self.assertEqual(manifest['grouping'], 'height')
            self.assertIsNone(manifest['questions_per_image'])
            self.assertEqual(manifest['max_height'], 1000)
            self.assertEqual([q['id'] for p in manifest['pages'] for q in p['questions']],
                             ['q1', 'q2', 'q3'])
            self.assertTrue(manifest['pages'][0]['oversize'])
            self.assertEqual(manifest['pages'][0]['question_count'], 1)
            self.assertEqual(manifest['pages'][-1]['question_count'], 2)
            for page in manifest['pages']:
                self.assertTrue(page['height'] <= 1000 or page['question_count'] == 1)

    def test_extract_and_render_together(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            source, output = Path(temp)/'test.mhtml', Path(temp)/'result'
            message = EmailMessage()
            message.make_related()
            part = EmailMessage()
            part.set_content('<h3>测试作业</h3><div class="singleQuesId" data="q1">'
                             '<div class="Zy_TItle"><i>1</i><div class="fontLabel">'
                             '一起提取并渲染的题目</div></div></div>', subtype='html', charset='utf-8')
            message.attach(part)
            source.write_bytes(message.as_bytes())
            extract(source, output, render_every=5, image_renderer=self.renderer, render_width=960)
            manifest = json.loads((output/'rendered/manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['question_count'], 1)
            self.assertEqual(manifest['image_count'], 1)
            self.assertTrue((output/'rendered'/manifest['pages'][0]['image']).is_file())


if __name__ == '__main__':
    unittest.main()
