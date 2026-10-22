import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from answers_homework import merge_answers
from review_homework import index_questions, question_detail, copy_markdown, set_favorite
from test_answers import block, example

ROOT = Path(__file__).resolve().parents[1]


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.folder = Path(self.temp.name)
        self.rows = example(self.folder)
        (self.folder/'images').mkdir()
        from PIL import Image
        Image.new('RGB', (20, 30), 'red').save(self.folder/'images/circuit.png')

    def tearDown(self):
        self.temp.cleanup()

    def test_favorites_and_imported_solution_match_repeated_local_numbers(self):
        set_favorite(self.folder, 3, True)
        self.assertEqual([r['order'] for r in index_questions(self.folder)['questions'] if r['favorite']], [3])
        merge_answers(self.folder, block(self.rows[2], '数电参考解答'))
        detail = question_detail(self.folder, 3)
        self.assertIn('原题号 1', detail['text'])
        self.assertEqual(detail['answer'], '数电参考解答')
        self.assertEqual(question_detail(self.folder, 1)['answer'], '')
        set_favorite(self.folder, 3, False)
        self.assertFalse(question_detail(self.folder, 3)['favorite'])

    def test_copy_preserves_solution_formula_and_uses_absolute_local_image_url(self):
        merge_answers(self.folder, block(self.rows[0], '$I=U/R$'))
        result = copy_markdown(self.folder, 1)
        self.assertIn((self.folder/'images/circuit.png').as_uri(), result['text'])
        self.assertIn('$I=U/R$', result['text'])
        self.assertTrue(result['has_images'])
        self.assertTrue(result['has_answer'])
        self.assertFalse(copy_markdown(self.folder, 3)['has_answer'])
        command = [sys.executable, '-B', '-X', 'utf8', str(ROOT/'review_homework.py'),
                   'copy', str(self.folder), '--order', '1']
        process = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=10)
        self.assertEqual(process.returncode, 0, process.stdout+process.stderr)
        self.assertEqual(json.loads(process.stdout), result)

    def test_index_exposes_short_answer_conclusion(self):
        answer = '**结论**\n\n选 A（电流约为 1 mA）\n\n**推导过程**\n由欧姆定律可得。'
        merge_answers(self.folder, block(self.rows[0], answer))
        indexed = index_questions(self.folder)['questions']
        self.assertEqual(indexed[0]['conclusion'], '选 A（电流约为 1 mA）')
        self.assertEqual(indexed[1]['conclusion'], '')


if __name__ == '__main__':
    unittest.main()
