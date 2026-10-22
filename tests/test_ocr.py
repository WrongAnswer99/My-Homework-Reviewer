"""Exercise real archived fonts and both copied ONNX model sets."""
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from extract_homework import Archive, DOMParser, decode_text, extract, page_font
from ocr.engine import Engine
from ocr.question_ocr import QuestionOCR


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt', 'The copied OCRtest runtime is Windows x64')
class OCRIntegrationTests(unittest.TestCase):
    def first_font(self):
        source = ROOT / '学生学习页面.mhtml'
        if not source.exists():
            self.skipTest('真实样例未提供')
        archive = Archive(source)
        for part in archive.parts:
            if part.get_content_type() == 'text/html':
                root = DOMParser(decode_text(part)).root
                if root.find_class('singleQuesId'):
                    return page_font(root, archive, str(part.get('Content-Location', '')))
        self.fail('没有找到样例字体')

    def test_direct_question_ocr_across_three_archives_without_maps(self):
        sources = [ROOT / (stem + '.mhtml') for stem in
                   ('学生学习页面', '学生学习页面2', '学生学习页面3')]
        if not all(source.exists() for source in sources):
            self.skipTest('三份真实样例未提供')
        reference = json.loads((ROOT / 'tests/fixtures/questions_reference.json').read_text(encoding='utf-8'))
        original_open = Path.open

        def forbid_maps(path, *args, **kwargs):
            if path.name in {'font_map.json', 'ocr_cache.json'}:
                raise AssertionError('整题 OCR 不应访问字形映射或识别缓存')
            return original_open(path, *args, **kwargs)

        with tempfile.TemporaryDirectory(dir=ROOT) as temp, QuestionOCR() as resolver:
            with patch.object(Path, 'open', forbid_maps):
                for source, count, images in zip(sources, (80, 120, 35), (13, 67, 27)):
                    with self.subTest(source=source.name):
                        output = Path(temp)/source.stem
                        report = extract(source, output, resolver)
                        self.assertEqual(report['question_count'], count)
                        self.assertEqual(report['image_count'], images)
                        self.assertEqual(report['ocr']['model'], 'v4')
                        question_images = list((output/'ocr_images').glob('question_*.png'))
                        self.assertEqual(len(question_images), report['ocr']['question_images'])
                        self.assertGreater(len(question_images), 0)
                        self.assertLessEqual(len(question_images), count)
                        text = (output/'questions.md').read_text(encoding='utf-8')
                        self.assertNotIn('\ufffd', text)
                        self.assertTrue(all((output/ref).is_file() for ref in
                                            re.findall(r'!\[.*?\]\((images/[^)]+)\)', text)))
                        if source == sources[0]:
                            # Source identity, order, question types and original
                            # image/formula references are independent of OCR.
                            for actual, expected in zip(report['assignments'], reference['assignments']):
                                self.assertEqual(len(actual['questions']), len(expected['questions']))
                                for aq, eq in zip(actual['questions'], expected['questions']):
                                    for key in ('id', 'number', 'type'):
                                        self.assertEqual(aq[key], eq[key])
                                    self.assertEqual([o['label'] for o in aq['options']],
                                                     [o['label'] for o in eq['options']])
                                    for atext, etext in zip([aq['stem']] + [o['text'] for o in aq['options']],
                                                           [eq['stem']] + [o['text'] for o in eq['options']]):
                                        self.assertEqual(re.findall(r'!\[[^\n]*?\]\(images/[^)]+\)', atext),
                                                         re.findall(r'!\[[^\n]*?\]\(images/[^)]+\)', etext))
            self.assertFalse(list(Path(temp).rglob('font_map.json')))
            self.assertFalse(list(Path(temp).rglob('ocr_cache.json')))

    def test_short_question_with_disconnected_strokes_and_repeated_recognition(self):
        blob = self.first_font()
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            for model in ('v4', 'v6tiny'):
                with QuestionOCR(model=model) as resolver:
                    for index in range(2):
                        pieces = [{'runs': [(chr(0x5E07), True)], 'original': '',
                                   'encrypted': True, 'prefix': '题干：'}]
                        resolver.recognize(blob, pieces, Path(temp)/f'{model}_{index}.png')
                        self.assertEqual(pieces[0]['result'], '二')
                    self.assertEqual(resolver.question_count, 2)

    def test_blank_image_and_engine_lifetime(self):
        for model in ('v4', 'v6tiny'):
            with Engine(model=model) as engine:
                self.assertEqual(engine.recognize(Image.new('RGB', (160, 80), 'white')), [])
            with self.assertRaisesRegex(OSError, '已关闭'):
                engine.recognize(Image.new('RGB', (160, 80), 'white'))


if __name__ == '__main__':
    unittest.main()
