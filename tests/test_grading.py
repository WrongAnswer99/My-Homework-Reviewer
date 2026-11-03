"""Grading information stays at the end of each question in every output."""
import json
import tempfile
import unittest
from pathlib import Path

from answers_homework import build_document, load_questions
from api_config import new_profile
from api_homework import build_payload
from question_metadata import graded_answer_note
from render_homework import BrowserRenderer, batch_html, group_questions
from review_homework import question_text


ROOT = Path(__file__).resolve().parents[1]


class GradingOutputTests(unittest.TestCase):
    def test_all_question_outputs_keep_student_answer_after_options(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            folder = Path(temp)
            questions = [dict(id=str(i), number=str(i), type='单选题', stem='测试题干',
                              options=[{'label': 'A', 'text': '最后一个选项'}],
                              student_answer=answer, grading=grading)
                         for i, (answer, grading) in enumerate(
                             [('A', 'correct'), ('B', 'incorrect')], 1)]
            report = {'source_file': 'graded.mhtml', 'question_count': 2, 'assignment_count': 1,
                      'warnings': [], 'assignments': [{'title': '测试作业', 'questions': questions}]}
            (folder/'questions.json').write_text(json.dumps(report), encoding='utf-8')
            _, _, rows, _ = load_questions(folder)
            document = build_document(report, rows, {})
            content = batch_html(report, group_questions(report, 2)[0], folder, {})
            profile = new_profile()
            profile.update(base_url='https://example.test/v1', api_key='test-only',
                           model='test-model', send_image=False)
            payload, _ = build_payload(profile, folder, 1, 2)
            api_text = payload['messages'][-1]['content']
            for q, row in zip(questions, rows):
                note = graded_answer_note(q)
                self.assertTrue(question_text(row).rstrip().endswith(note))
                self.assertIn(note, document)
                self.assertIn(note, api_text)
                self.assertIn(f'<p class="graded-answer">{note}</p>', content)
            self.assertGreater(content.index('我的答案：A'), content.index('最后一个选项'))
            self.assertNotIn('正确答案', content)
            # A real browser checks the same text used to measure and screenshot cards.
            with BrowserRenderer(960) as renderer:
                renderer.measure(content)
                self.assertEqual(renderer.page.locator('.graded-answer').all_text_contents(),
                                 ['我的答案：A，该题正确。', '我的答案：B，该题错误。'])
                self.assertTrue(renderer.page.locator('article').first.evaluate(
                    "e => e.lastElementChild.classList.contains('graded-answer')"))

    def test_missing_grade_is_not_assumed_incorrect_and_markup_is_escaped(self):
        self.assertEqual(graded_answer_note({'student_answer': 'A'}), '')
        self.assertEqual(graded_answer_note({}), '')
        self.assertEqual(graded_answer_note({'student_answer': '', 'grading': 'incorrect'}),
                         '我的答案：未作答，该题错误。')
        self.assertEqual(graded_answer_note({'student_answer': '<b>_x*</b>', 'grading': 'partial'},
                                           markdown=True),
                         r'我的答案：\<b\>\_x\*\</b\>，该题部分正确。')


if __name__ == '__main__':
    unittest.main()
