import json
import tempfile
import unittest
from pathlib import Path

from answers_homework import load_questions, make_prompt, merge_answers, parse_answers


ROOT = Path(__file__).resolve().parents[1]


def example(folder):
    report = {'source_file': '电路作业.mhtml', 'question_count': 3, 'assignment_count': 2,
              'warnings': ['原题中有一张图未保存'], 'assignments': [
                  {'title': '电路', 'questions': [
                      {'id': 'a', 'number': '1', 'type': '填空题', 'blank_count': 1,
                       'stem': '求电流____。\n![题图](images/circuit.png)', 'options': []},
                      {'id': 'b', 'number': '2', 'type': '单选题', 'stem': '选择工作区。',
                       'options': [{'label': 'A', 'text': '放大区'}]}]},
                  {'title': '数电', 'questions': [
                      {'id': 'c', 'number': '1', 'type': '判断题', 'stem': '原题号重复。', 'options': []}]}]}
    (folder/'questions.json').write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
    (folder/'questions.md').write_text('原文保留', encoding='utf-8')
    return load_questions(folder)[2]


def block(row, body='**结论**\n\n测试解答'):
    return f'## 【解答 {row["key"]}】\n{body}\n【结束 {row["key"]}】'


class AnswerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.folder = Path(self.temp.name)
        self.rows = example(self.folder)

    def tearDown(self):
        self.temp.cleanup()

    def test_prompt_maps_repeated_local_numbers_by_global_order(self):
        prompt = make_prompt(self.folder, 2, 3)
        self.assertIn('图片顺序第 2 题', prompt)
        self.assertIn('作业 2「数电」，原题号 1', prompt)
        self.assertIn(self.rows[2]['key'], prompt)
        self.assertNotIn(self.rows[0]['key'], prompt)
        with self.assertRaises(ValueError):
            make_prompt(self.folder, 0, 3)

    def test_accumulates_batches_replaces_same_question_preserves_math_and_images(self):
        body = '**结论**\n\n$I=\\frac{U}{R}$\n\n$$\nI=2\\,\\mathrm{mA}\n$$\n\n|量|值|\n|---|---|\n|I|2 mA|'
        merge_answers(self.folder, block(self.rows[0], body))
        result = merge_answers(self.folder, block(self.rows[2], '数电题测试解答'))
        self.assertEqual(result['answered'], [1, 3])
        document = Path(result['document']).read_text(encoding='utf-8')
        self.assertIn(body, document)
        self.assertIn('![题图](images/circuit.png)', document)
        self.assertIn('- **A.** 放大区', document)
        self.assertIn('填空数量：1', document)
        self.assertIn('总第 3 题', document)
        self.assertIn('尚未导入解答', document)
        self.assertIn('原题中有一张图未保存', document)
        merge_answers(self.folder, block(self.rows[0], '修订后的测试解答'))
        store = json.loads((self.folder/'answers.json').read_text(encoding='utf-8'))
        self.assertEqual(len(store['answers']), 2)
        self.assertEqual(store['answers'][self.rows[0]['key']], '修订后的测试解答')
        self.assertEqual((self.folder/'questions.md').read_text(encoding='utf-8'), '原文保留')

    def test_excluded_api_question_is_not_replaced_by_other_card_paste(self):
        merge_answers(self.folder, block(self.rows[0], 'API 解答'))
        mixed = block(self.rows[0], '粘贴的旧答案')+'\n\n'+block(self.rows[1], '第二题新解答')
        result = merge_answers(self.folder, mixed, exclude_range=(1, 1))
        self.assertEqual(result['updated'], [2])
        store = json.loads((self.folder/'answers.json').read_text(encoding='utf-8'))
        self.assertEqual(store['answers'][self.rows[0]['key']], 'API 解答')
        self.assertEqual(store['answers'][self.rows[1]['key']], '第二题新解答')
        self.assertEqual(merge_answers(self.folder, block(self.rows[0], '仍是旧答案'),
                                       exclude_range=(1, 1))['status'], 'protected')
        self.assertEqual(merge_answers(self.folder, '普通笔记', exclude_range=(1, 1))['status'],
                         'unrecognized')
        with self.assertRaisesRegex(ValueError, '范围无效'):
            merge_answers(self.folder, mixed, exclude_range=(0, 1))

    def test_invalid_or_incomplete_response_is_atomic(self):
        merge_answers(self.folder, block(self.rows[0]))
        original = {name: (self.folder/name).read_bytes()
                    for name in ('answers.json', 'questions_with_answers.md')}
        bad = [block(self.rows[0])+'\n'+block(self.rows[0]),
               block(self.rows[1])+'\n'+f'## 【解答 {self.rows[2]["key"]}】\n未结束',
               block(self.rows[1]).replace(self.rows[1]['key'], 'H000000000000-Q0002'),
               f'## 【解答 {self.rows[0]["key"]}】\n【结束 {self.rows[0]["key"]}】',
               f'## 【解答 {self.rows[0]["key"]}】\n正文\n【结束 {self.rows[1]["key"]}】',
               '## 【解答 Q1】\n正文\n【结束 Q1】']
        for response in bad:
            with self.subTest(response=response), self.assertRaises(ValueError):
                merge_answers(self.folder, response)
            for name, content in original.items():
                self.assertEqual((self.folder/name).read_bytes(), content)
        self.assertFalse(list(self.folder.glob('.answers-*')))

    def test_code_fences_and_outer_markdown_wrapper(self):
        # A marker in an example code block is ordinary solution text.
        fake = block(self.rows[1], '示例')
        body = f'推导：\n```text\n{fake}\n```\n## 核对\n$V=1$'
        response = '```markdown\n'+block(self.rows[0], body)+'\n```'
        parsed = parse_answers(response, {r['key'] for r in self.rows})
        self.assertEqual(parsed, {self.rows[0]['key']: body})
        result = merge_answers(self.folder, response)
        document = Path(result['document']).read_text(encoding='utf-8')
        self.assertIn(fake, document)
        self.assertIn('##### 核对', document)

    def test_unmarked_text_never_guesses_local_numbers(self):
        result = merge_answers(self.folder, '## 第1题\nA\n## 第2题\nB')
        self.assertEqual(result['status'], 'unrecognized')
        self.assertIsNone(result['document'])
        self.assertFalse((self.folder/'answers.json').exists())

    def test_question_edits_invalidate_old_answers(self):
        merge_answers(self.folder, block(self.rows[0]))
        report = json.loads((self.folder/'questions.json').read_text(encoding='utf-8'))
        report['assignments'][0]['questions'][0]['stem'] = '已修改的题目'
        (self.folder/'questions.json').write_text(json.dumps(report), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '不属于当前'):
            merge_answers(self.folder, block(self.rows[0]))
        new_rows = load_questions(self.folder)[2]
        with self.assertRaisesRegex(ValueError, '已有解答记录与当前题目不匹配'):
            merge_answers(self.folder, block(new_rows[0]))


if __name__ == '__main__':
    unittest.main()
