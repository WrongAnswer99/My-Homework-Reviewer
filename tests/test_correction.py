import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from answers_homework import merge_answers
from api_config import new_profile, save_profiles
from review_correction import correction_prompt, question_image
from review_homework import copy_markdown, index_questions, question_detail, save_correction
from test_answers import block, example
from test_api import api_server, sse


ROOT = Path(__file__).resolve().parents[1]


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.folder = Path(self.temp.name)
        self.rows = example(self.folder)
        (self.folder/'images').mkdir()
        Image.new('RGB', (20, 30), 'white').save(self.folder/'images/circuit.png')

    def tearDown(self):
        self.temp.cleanup()

    def test_note_and_revised_answer_are_separate_from_original_answer(self):
        merge_answers(self.folder, block(self.rows[0], '**结论**\n\n旧答案'))
        saved = save_correction(self.folder, 1, '平台答案：选 A，因为电流方向相反。',
                                '**结论**\n\n选 A\n\n**推导过程**\n按电流方向重新计算。')
        self.assertEqual(saved['conclusion'], '选 A')
        detail = question_detail(self.folder, 1)
        self.assertIn('旧答案', detail['answer'])
        self.assertIn('平台答案', detail['correction_note'])
        self.assertIn('重新计算', detail['corrected_answer'])
        self.assertEqual(index_questions(self.folder)['questions'][0]['conclusion'], '选 A')
        copied = copy_markdown(self.folder, 1)['text']
        self.assertIn('### 修正答案', copied)
        self.assertIn('按电流方向重新计算', copied)
        self.assertNotIn('旧答案', copied)
        self.assertNotIn('### 纠错信息', copied)
        prompt = correction_prompt(self.folder, 1, '平台答案', detail['answer'])
        self.assertIn('平台答案', prompt)
        self.assertIn('旧答案', prompt)
        self.assertIn('最终结论必须以官方答案为准', prompt)
        self.assertIn('明确指出与官方答案或解析冲突的步骤', prompt)
        with self.assertRaisesRegex(ValueError, '官方答案'):
            correction_prompt(self.folder, 1, ' ', detail['answer'])

    def test_single_question_image_is_cached(self):
        class Renderer:
            def __init__(self, width):
                self.width = width

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def capture(self, html, path):
                self_outer.assertIn('求电流', html)
                self_outer.assertNotIn('选择工作区', html)
                Image.new('RGB', (1280, 80), 'white').save(path)

        self_outer = self
        with patch('review_correction.BrowserRenderer', Renderer):
            image = question_image(self.folder, 1)
        self.assertTrue(image.is_file())
        with patch('review_correction.BrowserRenderer', side_effect=AssertionError('cache missed')):
            self.assertEqual(question_image(self.folder, 1), image)

    def test_correction_api_sends_note_original_answer_and_single_image(self):
        merge_answers(self.folder, block(self.rows[0], '旧解答'))
        image = self.folder/'review_images'/'question_0001.png'
        image.parent.mkdir()
        Image.new('RGB', (40, 50), 'white').save(image)
        values = {'note': '平台给出正确答案 A，旧解答方向反了。', 'answer': ''}
        input_file = self.folder/'input.json'
        input_file.write_text(json.dumps(values, ensure_ascii=False), encoding='utf-8')
        profile = new_profile()
        profile.update(name='测试模型', model='mock-vision', api_key='TEST-KEY-ONLY')
        config = self.folder/'api_config.json'
        response = block(self.rows[0], '**结论**\n\nA\n\n**推导过程**\n方向已修正。')
        with api_server(sse(response), 'text/event-stream') as (url, requests):
            profile['base_url'] = url
            save_profiles(config, [profile])
            command = [sys.executable, '-B', '-X', 'utf8', str(ROOT/'review_correction.py'),
                       'api', str(self.folder), '--order', '1', '--input', str(input_file),
                       '--config', str(config), '--profile', profile['id']]
            run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                 encoding='utf-8', timeout=20)
        self.assertEqual(run.returncode, 0, run.stdout+'\n'+run.stderr)
        events = [json.loads(line[6:]) for line in run.stdout.splitlines() if line.startswith('@@API ')]
        self.assertEqual(events[-1]['answer'], '**结论**\n\nA\n\n**推导过程**\n方向已修正。')
        content = requests[0]['body']['messages'][-1]['content']
        self.assertIn(values['note'], content[0]['text'])
        self.assertIn('旧解答', content[0]['text'])
        self.assertIn('最终结论必须以官方答案为准', content[0]['text'])
        self.assertNotIn('请解答我随后发送的这张', content[0]['text'])
        self.assertTrue(content[1]['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertNotIn('TEST-KEY-ONLY', run.stdout+run.stderr)


if __name__ == '__main__':
    unittest.main()
