import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from extract_homework import extract
from official_correction import correction_plan, import_results
from review_correction import question_image
from review_homework import question_detail, save_correction
from test_answers import example
from test_extract import archive

ROOT = Path(__file__).resolve().parents[1]


def feedback(qid, icons='marking_cuo', answer='测试答案', analysis=''):
    return f'''<div class="singleQuesId" data="{qid}">
      <div class="myAnswerBx">{''.join(f'<span class="{i}"></span>' for i in icons.split())}</div>
      <div class="correctAnswerBx"><span class="answerFont">正确答案：</span>
        <div class="answerCon">{answer}</div></div>
      <div class="answerKeyBx"><span class="answerFont">答案解析：</span>
        <div class="answerCon">{analysis}</div></div></div>'''


class OfficialCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.folder = Path(self.temp.name)
        self.rows = example(self.folder)
        self.archive = self.folder/'result.mhtml'

    def tearDown(self):
        self.temp.cleanup()

    def test_partial_blanks_images_preserve_drafts_and_repeat_import(self):
        buffer = io.BytesIO()
        Image.new('RGB', (20, 30), 'white').save(buffer, 'PNG')
        html = feedback('a', 'marking_dui marking_cuo', '第一空：1;第二空：2',
                        '图解 <img src="cid:analysis">')+feedback('c', 'marking_dui')
        archive(self.archive, html, [('analysis', 'image/png', buffer.getvalue())])
        save_correction(self.folder, 1, '手工补充', '原修正答案')
        result = import_results(self.folder, self.archive)
        self.assertEqual((result['imported'], result['correct']), (1, 1))
        detail = question_detail(self.folder, 1)
        self.assertIn('手工补充', detail['correction_note'])
        self.assertIn('第一空：1;第二空：2', detail['correction_note'])
        self.assertIn('图解', detail['correction_note'])
        self.assertNotIn('答案解析：', detail['correction_note'])
        self.assertEqual(detail['corrected_answer'], '原修正答案')
        relative = re.search(r'\]\((images/[^)]+)\)', detail['correction_note'])[1]
        self.assertEqual((self.folder/relative).read_bytes(), buffer.getvalue())
        import_results(self.folder, self.archive)
        self.assertEqual(question_detail(self.folder, 1)['correction_note'], detail['correction_note'])
        self.assertEqual([q['order'] for q in correction_plan(self.folder)['questions']], [1])
        self.assertFalse((self.folder/'answers.json').exists())

    def test_unmatched_or_missing_image_does_not_write_partial_notes(self):
        save_correction(self.folder, 1, '原建议', '原答案')
        before = (self.folder/'review.json').read_bytes()
        for html in (feedback('a')+feedback('unrelated'),
                     feedback('a', analysis='<img src="cid:missing">')):
            with self.subTest(html=html):
                archive(self.archive, html)
                with self.assertRaises(ValueError):
                    import_results(self.folder, self.archive)
                self.assertEqual((self.folder/'review.json').read_bytes(), before)

    def test_shuffled_choice_letters_map_to_current_options(self):
        html = feedback('b', answer='B')
        html = html.replace('<div class="myAnswerBx">',
                            '<ul class="Zy_ulTop"><li><i>A、</i><a>截止区</a></li>'
                            '<li><i>B、</i><a>放大区</a></li></ul><div class="myAnswerBx">')
        archive(self.archive, html)
        result = import_results(self.folder, self.archive)
        self.assertIn('官方正确答案：A', result['notes'][0]['note'])
        self.assertIn('结果页答案：B', result['notes'][0]['note'])

    def test_feedback_is_rendered_into_manual_and_api_picture(self):
        (self.folder/'images').mkdir()
        Image.new('RGB', (20, 30), 'white').save(self.folder/'images/circuit.png')
        captured = []
        class Renderer:
            def __init__(self, width):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def capture(self, html, path):
                captured.append(html)
                Image.new('RGB', (100, 100), 'white').save(path)
        with patch('review_correction.BrowserRenderer', Renderer):
            first = question_image(self.folder, 1, note='官方图解 ![说明](images/circuit.png)')
            second = question_image(self.folder, 1, note='更新图解 ![说明](images/circuit.png)')
        self.assertNotEqual(first, second)
        self.assertIn('官方图解', captured[0])
        self.assertIn('data:image/png;base64,', captured[0])

    def test_unrelated_correct_archive_and_conflicting_duplicates_are_rejected(self):
        for html in (feedback('unrelated', 'marking_dui'),
                     feedback('a', answer='1')+feedback('a', answer='2')):
            with self.subTest(html=html):
                archive(self.archive, html)
                with self.assertRaises(ValueError):
                    import_results(self.folder, self.archive)
                self.assertFalse((self.folder/'review.json').exists())

    def test_real_archive_imports_four_wrong_questions_and_five_analysis_images(self):
        source = ROOT/'模电第四章.mhtml'
        results = ROOT/'数电第四章纠错.mhtml'
        if not source.exists() or not results.exists():
            self.skipTest('本地真实归档未提供')
        output = self.folder/'export'
        extract(source, output)
        result = import_results(output, results)
        self.assertEqual(result['imported'], 4)
        self.assertEqual(result['correct'], 31)
        self.assertEqual([n['order'] for n in result['notes']], [9, 14, 23, 24])
        self.assertEqual(sum(n['note'].count('![') for n in result['notes']), 5)
        for entry in result['notes']:
            for relative in re.findall(r'\]\((images/[^)]+)\)', entry['note']):
                self.assertTrue((output/relative).is_file())


if __name__ == '__main__':
    unittest.main()
