import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from extract_homework import extract
from ocr.question_ocr import QuestionOCR
from cli_progress import PROGRESS_PREFIX


ROOT = Path(__file__).resolve().parents[1]


def archive(path, html, images=()):
    message = EmailMessage()
    message.make_related()
    part = EmailMessage()
    part.set_content(html, subtype='html', charset='utf-8')
    part['Content-Location'] = 'https://example.test/work/view'
    message.attach(part)
    for cid, mime, data in images:
        part = EmailMessage()
        kind, subtype = mime.split('/')
        part.set_content(data, maintype=kind, subtype=subtype)
        part['Content-ID'] = '<' + cid + '>'
        message.attach(part)
    path.write_bytes(message.as_bytes())


def question(stem, options=''):
    return f'''<h3>测试作业</h3><div class="singleQuesId" data="q1">
    <div class="Zy_TItle"><i>1</i><div class="fontLabel">
    <span class="newZy_TItle">【单选题】</span>{stem}</div></div>
    <ul class="Zy_ulTop">{options}</ul></div>'''


class ExtractionTests(unittest.TestCase):
    def test_new_homework_editor_choice_labels_and_content(self):
        for qtype, label_class, role in [('单选题', 'num_option', 'radio'),
                                         ('多选题', 'num_option_dx', 'checkbox')]:
            with self.subTest(qtype=qtype), tempfile.TemporaryDirectory(dir=ROOT) as temp:
                source, output = Path(temp)/'test.mhtml', Path(temp)/'result'
                html = f'''<h2 class="mark_title">测试作业</h2>
                <div class="questionLi singleQuesId" typename="{qtype}" data="q1"
                     aria-label="题目 9. ">
                  <h3 class="mark_name">9.<span class="colorShallow">({qtype}, 5分)</span>
                    <p>选择正确的表达式。</p></h3>
                  <div class="stem_answer">
                    <div class="answerBg" role="{role}" aria-checked="true" aria-label="D 选择">
                      <span class="{label_class} check_answer_dx" data="D">A</span>
                      <div class="answer_p"><p>逻辑变量 x<sub>1</sub></p></div>
                    </div>
                    <div class="answerBg" role="{role}" aria-label="A 选择">
                      <span class="{label_class}" data="A">B</span>
                      <div class="answer_p"><img src="cid:formula"></div>
                    </div>
                  </div>
                  <input type="hidden" name="answer" value="D">
                </div>'''
                archive(source, html, [('formula', 'image/png', b'formula-image')])
                report = extract(source, output, resolver=SimpleNamespace(model='unused', question_count=0))
                q = report['assignments'][0]['questions'][0]
                self.assertEqual(q['number'], '9')
                self.assertEqual(q['type'], qtype)
                self.assertEqual(q['stem'], '选择正确的表达式。')
                self.assertEqual([o['label'] for o in q['options']], ['A', 'B'])
                self.assertEqual(q['options'][0]['text'], '逻辑变量 x<sub>1</sub>')
                self.assertIn('![题图](images/', q['options'][1]['text'])
                self.assertEqual(report['image_count'], 1)
                markdown = (output/'questions.md').read_text(encoding='utf-8')
                self.assertIn('- **A.** 逻辑变量 x<sub>1</sub>', markdown)
                self.assertIn('- **B.** ![题图](images/', markdown)
                self.assertNotIn('aria-checked', markdown)
                self.assertNotIn('answer', q)

    def test_new_homework_editor_real_multiple_choice_questions(self):
        source = ROOT/'数电第二章1.mhtml'
        if not source.exists():
            self.skipTest('新版多选题样例归档未提供')
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            output = Path(temp)/'result'
            report = extract(source, output, resolver=SimpleNamespace(model='unused', question_count=0))
            questions = report['assignments'][0]['questions']
            self.assertEqual(report['question_count'], 17)
            self.assertEqual([q['type'] for q in questions],
                             ['单选题']*8 + ['多选题']*3 + ['填空题']*3 + ['计算题']*3)
            self.assertTrue(all([o['label'] for o in q['options']] == list('ABCD')
                                for q in questions[:11]))
            self.assertTrue(all(not q['options'] for q in questions[11:]))
            self.assertEqual([o['text'] for o in questions[8]['options']],
                             ['开关的闭合、断开', '电位的高、低', '真与假', '电流的有、无'])
            self.assertEqual([o['text'] for o in questions[10]['options']],
                             ['真值表', '表达式', '逻辑图', '卡诺图'])
            self.assertEqual(report['warnings'], [])

    def test_new_homework_editor_extracts_questions_without_student_answers(self):
        source = ROOT/'数电第二章2.mhtml'
        if not source.exists():
            self.skipTest('新版作业样例归档未提供')
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            output = Path(temp)/'result'
            report = extract(source, output, resolver=SimpleNamespace(model='unused', question_count=0))
            self.assertEqual(report['assignments'][0]['title'], '第2章 数字逻辑基础作业2')
            self.assertEqual(report['question_count'], 13)
            self.assertEqual(report['image_count'], 19)
            self.assertEqual(report['warnings'], [])
            questions = report['assignments'][0]['questions']
            self.assertEqual([q['type'] for q in questions], ['单选题']*5+['计算题']*8)
            self.assertTrue(all([o['label'] for o in q['options']] == list('ABCD') for q in questions[:5]))
            self.assertTrue(all(not q['options'] for q in questions[5:]))
            self.assertIn('最简与或式', questions[0]['stem'])
            self.assertIn('![题图](images/', questions[0]['stem'])
            self.assertIn('约束条件为', questions[-1]['stem'])
            self.assertEqual(questions[5]['number'], '6')
            markdown = (output/'questions.md').read_text(encoding='utf-8')
            self.assertNotIn('段落格式字体字号', markdown)
            self.assertNotIn('取反B', questions[5]['stem'])
            self.assertTrue(all(path.is_file() for path in (output/'images').glob('*.png')))
            self.assertEqual(len(list((output/'images').glob('*.png'))), 19)

    @unittest.skipUnless(os.name == 'nt', 'Windows ACL regression')
    def test_windows_export_inherits_parent_permissions(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            source, output = Path(temp)/'test.mhtml', Path(temp)/'output'
            archive(source, question('测试权限继承'))
            extract(source, output)
            quoted = "'" + str(output).replace("'", "''") + "'"
            script = f'(Get-Acl -LiteralPath {quoted}).AreAccessRulesProtected'
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                                     '-Command', script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip().lower(), 'false')

    def test_additional_real_archives_and_fill_in_questions(self):
        cases = [('学生学习页面2.mhtml', [18, 20, 18, 13, 3, 5, 3, 40], 67, 88, 89),
                 ('学生学习页面3.mhtml', [18, 17], 27, 44, 34)]
        for filename, counts, images, blanks, references in cases:
            source = ROOT / filename
            if not source.exists():
                continue
            with self.subTest(filename=filename), tempfile.TemporaryDirectory(dir=ROOT) as temp:
                output = Path(temp) / 'output'
                result = extract(source, output)
                self.assertEqual([len(a['questions']) for a in result['assignments']], counts)
                self.assertEqual(result['question_count'], sum(counts))
                self.assertEqual(result['image_count'], images)
                questions = [q for a in result['assignments'] for q in a['questions']]
                self.assertEqual(sum(q.get('blank_count', 0) for q in questions), blanks)
                self.assertTrue(all(not q['options'] for q in questions if q.get('blank_count')))
                md = (output/'questions.md').read_text(encoding='utf-8')
                refs = re.findall(r'!\[.*?\]\((images/[^)]+)\)', md)
                self.assertEqual(len(refs), references)
                self.assertEqual(len(set(refs)), images)
                self.assertTrue(all((output/ref).is_file() for ref in refs))
                self.assertNotIn('\ufffd', md)
                self.assertNotIn('****', md)
                if filename == '学生学习页面2.mhtml':
                    first = questions[0]
                    self.assertEqual(first['type'], '填空题')
                    self.assertEqual(first['blank_count'], 4)
                    self.assertIn('已知某晶体管', first['stem'])
                    self.assertIn('如图所示', result['assignments'][0]['questions'][17]['stem'])
                    self.assertIn('任一时刻', result['assignments'][3]['questions'][4]['options'][2]['text'])
                    self.assertIn('直接耦合', result['assignments'][2]['questions'][0]['stem'])
                    self.assertEqual(result['warnings'], [])
                else:
                    first = result['assignments'][1]['questions'][0]
                    self.assertEqual(first['type'], '填空题')
                    self.assertEqual(first['displayed_type'], '单选题')
                    self.assertEqual(first['blank_count'], 1)
                    self.assertEqual(len(result['warnings']), 1)
                    self.assertIn('金属', result['assignments'][0]['questions'][3]['options'][0]['text'])

    def test_batch_continues_after_one_file_fails(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            sources, output = Path(temp) / 'sources', Path(temp) / 'output'
            sources.mkdir()
            archive(sources / 'a.mhtml', '<html>只有导航</html>')
            archive(sources / 'b.MHT', question('成功提取的题目'))
            result = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT/'extract_homework.py'),
                                     str(sources), '-o', str(output)],
                                    capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 1)
            self.assertNotIn(PROGRESS_PREFIX, result.stdout)
            self.assertFalse((output/'a').exists())
            self.assertTrue((output/'b'/'questions.md').is_file())
            self.assertIn('成功提取的题目', (output/'b'/'questions.md').read_text(encoding='utf-8'))

    def test_cli_progress_reports_questions_and_selected_model(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            source, output = Path(temp)/'test.mhtml', Path(temp)/'output'
            archive(source, ''.join(question(f'测试题目 {index}') for index in range(3)))
            result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(ROOT/'extract_homework.py'),
                                     str(source), '-o', str(output), '--ocr-model', 'v6tiny', '--progress-json'],
                                    capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            events = [json.loads(line[len(PROGRESS_PREFIX):]) for line in result.stdout.splitlines()
                      if line.startswith(PROGRESS_PREFIX)]
            self.assertEqual(events[0]['stage'], 'read')
            self.assertEqual([e['current'] for e in events if e['stage'] == 'questions'
                              and e['message'].startswith('已处理')], [1, 2, 3])
            self.assertEqual(events[-1]['stage'], 'saved')
            report = json.loads((output/source.stem/'questions.json').read_text(encoding='utf-8'))
            self.assertEqual(report['ocr']['model'], 'v6tiny')

    def test_sample_counts_text_and_local_images(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            output = Path(temp) / 'result'
            source = ROOT / '学生学习页面.mhtml'
            if not source.exists():
                self.skipTest('样例归档未提供')
            report = extract(source, output)
            self.assertEqual([len(a['questions']) for a in report['assignments']], [35, 35, 10])
            self.assertEqual(report['image_count'], 13)
            self.assertEqual(report['question_count'], 80)
            first = report['assignments'][0]['questions'][0]
            self.assertEqual(first['stem'], '硅二极管的正向电压从0.65V增大10%,则流过的正向电流增大____。')
            self.assertEqual(first['options'][0], {'label': 'A', 'text': '小于10%'})
            self.assertIn('并联连接', report['assignments'][2]['questions'][3]['options'][0]['text'])
            md = (output / 'questions.md').read_text(encoding='utf-8')
            refs = re.findall(r'!\[.*?\]\((images/[^)]+)\)', md)
            self.assertTrue(refs)
            self.assertEqual(len(set(refs)), 13)
            self.assertTrue(all((output / ref).is_file() for ref in refs))
            self.assertNotIn('https://', md)
            self.assertEqual(len(report['warnings']), 1)
            self.assertIn('第 13 题', report['warnings'][0])
            self.assertEqual(json.loads((output/'questions.json').read_text(encoding='utf-8')), report)

    def test_cid_images_deduplicate_and_display_labels(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            source = Path(temp) / 'test.mhtml'
            html = question('中文题干<img src="cid:photo">', '''
                <li aria-label="D 选项"><label><span class="num_option" data="D">A</span></label>
                <a class="after">选项<img src="cid:photo"></a></li>''')
            archive(source, html, [('photo', 'image/png', b'test-image-bytes')])
            result = extract(source, Path(temp) / 'output')
            self.assertEqual(result['image_count'], 1)
            self.assertEqual(result['assignments'][0]['questions'][0]['options'][0]['label'], 'A')
            self.assertEqual(result['warnings'], [])

    def test_missing_picture_is_explicit(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            source = Path(temp) / 'test.mhtml'
            archive(source, question('请看<img src="https://example.test/absent.png">'))
            result = extract(source, Path(temp) / 'output')
            self.assertEqual(result['image_count'], 0)
            self.assertIn('图片缺失', result['assignments'][0]['questions'][0]['stem'])
            self.assertEqual(len(result['warnings']), 1)

    def test_empty_page_leaves_no_partial_export(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            source, output = Path(temp) / 'test.mhtml', Path(temp) / 'output'
            archive(source, '<html><body>导航，没有作业</body></html>')
            with self.assertRaisesRegex(ValueError, '没有找到题目'):
                extract(source, output)
            self.assertFalse(output.exists())
            self.assertFalse(list(Path(temp).glob('.extract-*')))

    def test_existing_notes_are_not_overwritten(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            source, output = Path(temp) / 'test.mhtml', Path(temp) / 'output'
            archive(source, question('题干'))
            output.mkdir()
            note = output / 'questions.md'
            note.write_text('已有复习笔记', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '输出目录已存在'):
                extract(source, output)
            self.assertEqual(note.read_text(encoding='utf-8'), '已有复习笔记')

    def test_ocr_failure_leaves_no_partial_export(self):
        source = ROOT / '学生学习页面.mhtml'
        if not source.exists():
            self.skipTest('样例归档未提供')
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            output = Path(temp) / 'output'
            with QuestionOCR() as resolver:
                with patch('ocr.question_ocr.Engine') as engine:
                    engine.return_value.recognize.return_value = []
                    with self.assertRaisesRegex(ValueError, 'v4 未能识别整题'):
                        extract(source, output, resolver)
            self.assertFalse(output.exists())
            self.assertFalse(list(Path(temp).glob('.extract-*')))


if __name__ == '__main__':
    unittest.main()
