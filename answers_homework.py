"""Generate answer prompts and merge pasted Markdown, entirely offline."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from prompt_config import ANALOG_PROMPT, FORMULA_GUIDANCE, add_prompt_arguments, resolve_prompt

sys.dont_write_bytecode = True

START = re.compile(r'^#{1,6}\s+【解答\s+(H[0-9a-f]{12}-Q\d{4,})】\s*$')
END = re.compile(r'^【结束\s+(H[0-9a-f]{12}-Q\d{4,})】\s*$')
FENCE = re.compile(r'^\s{0,3}(`{3,}|~{3,})(.*)$')


def load_questions(source):
    source = Path(source)
    if source.is_dir():
        source = source/'questions.json'
    report = json.loads(source.read_text(encoding='utf-8-sig'))
    rows = []
    for assignment_index, assignment in enumerate(report['assignments'], 1):
        for question in assignment['questions']:
            rows.append({'order': len(rows)+1, 'assignment': assignment_index,
                         'title': assignment['title'], 'question': question})
    if not rows:
        raise ValueError('没有可整理的题目')
    signature = json.dumps({'source_file': report['source_file'], 'rows': rows},
                           ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    document_id = 'H'+hashlib.sha256(signature.encode('utf-8')).hexdigest()[:12]
    for row in rows:
        row['key'] = f'{document_id}-Q{row["order"]:04d}'
    return source.resolve(), report, rows, document_id


def make_prompt(source, first=1, last=None, prompt_text=None):
    _, report, rows, _ = load_questions(source)
    last = last if last is not None else len(rows)
    if not 1 <= first <= last <= len(rows):
        raise ValueError('提示词的题目范围无效')
    chosen = rows[first-1:last]
    mapping = '\n'.join(f'- 图片顺序第 {index} 题 → {row["key"]}；作业 {row["assignment"]}'
                        f'「{row["title"]}」，原题号 {row["question"]["number"]}。'
                        for index, row in enumerate(chosen, 1))
    template = '\n\n'.join(f'## 【解答 {row["key"]}】\n'
                           '**结论**\n\n（填写本题答案）\n\n'
                           '**推导过程**\n\n（填写详细步骤）\n\n'
                           '**检查**\n\n（填写核对过程或无法确定的条件）\n\n'
                           f'【结束 {row["key"]}】' for row in chosen)
    prompt_text = ANALOG_PROMPT if prompt_text is None else prompt_text
    if not isinstance(prompt_text, str) or not prompt_text.strip():
        raise ValueError('提示词正文不能为空')
    return f'''{prompt_text.strip()}

来源：{report['source_file']}；本批为整份资料的第 {first}–{last} 题，共 {len(chosen)} 题。

以下映射以图片中题目的出现顺序为准。图片中的原题号可能在不同作业中重复，必须使用指定的完整标识，不要仅按原题号编号：
{mapping}

输出要求：
1. 只输出 Markdown 解答，保留每道题的完整起始标题和结束行，标识必须逐字照抄。
2. 每题独立写「结论」「推导过程」「检查」，用加粗小标题；不要把多个题的答案合在一个标记内，不要复述整份题目。
3. {FORMULA_GUIDANCE} 保留必要的表格和局部代码块。
4. 包含本批全部题目，即使无法解答，也要保留对应标记并说明原因。不要在标记之外添加总述。

严格使用以下格式，将括号中的说明替换为实际解答：

{template}
'''


def parse_answers(text, valid_keys):
    """Require complete, document-bound blocks; never guess repeated local numbers."""
    text = text.replace('\r\n', '\n').replace('\r', '\n').strip().lstrip('\ufeff')
    lines = text.splitlines()
    # Some chat copy buttons wrap the entire response in a Markdown code fence.
    if lines and re.fullmatch(r'\s*```(?:markdown|md)?\s*', lines[0], re.I) and lines[-1].strip() == '```':
        lines = lines[1:-1]
    answers, active, body, fence = {}, None, [], None
    for line in lines:
        match = FENCE.match(line)
        if fence:
            if active:
                body.append(line)
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = None
            continue
        if match:
            fence = match[1]
            if active:
                body.append(line)
            continue
        start, end = START.fullmatch(line.strip()), END.fullmatch(line.strip())
        if start:
            key = start[1]
            if key not in valid_keys:
                raise ValueError(f'标识 {key} 不属于当前题目文件，请复制当前卡片的提示词重新生成')
            if active:
                raise ValueError(f'{active} 缺少结束标记；请粘贴完整回复')
            if key in answers:
                raise ValueError(f'回复中 {key} 重复，未导入；请每题只保留一份解答')
            active, body = key, []
        elif end:
            if end[1] != active:
                raise ValueError('结束标记与题目不匹配，未导入；请检查完整标识')
            answer = '\n'.join(body).strip()
            if not answer:
                raise ValueError(f'{active} 的解答为空，未导入')
            answers[active] = answer
            active, body = None, []
        elif '【解答 ' in line or '【结束 ' in line:
            raise ValueError('解答标记格式不正确，未导入；请使用“复制提示词”规定的格式')
        elif active:
            body.append(line)
    if active:
        raise ValueError(f'{active} 缺少结束标记；请粘贴完整回复')
    return answers


def load_store(directory, document_id, keys):
    path = directory/'answers.json'
    if not path.exists():
        return {'version': 1, 'document_id': document_id, 'answers': {}}
    store = json.loads(path.read_text(encoding='utf-8'))
    if store.get('version') != 1 or store.get('document_id') != document_id:
        raise ValueError('已有解答记录与当前题目不匹配，未覆盖')
    answers = store.get('answers')
    if not isinstance(answers, dict) or any(k not in keys or not isinstance(v, str) or not v.strip()
                                            for k, v in answers.items()):
        raise ValueError('已有解答记录格式损坏，未覆盖')
    return store


def answer_headings(text):
    """Keep solution headings beneath the question; leave fenced code unchanged."""
    result, fence = [], None
    for line in text.splitlines():
        match = FENCE.match(line)
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = None
        elif match:
            fence = match[1]
        else:
            line = re.sub(r'^#{1,6}\s+', '##### ', line)
        result.append(line)
    return '\n'.join(result)


def build_document(report, rows, answers):
    lines = ['# 课后作业 · 题目与解答', '', f'来源文件：{report["source_file"]}', '',
             f'共 {len(report["assignments"])} 份作业，{len(rows)} 道题；'
             f'已导入 {len(answers)} 道解答，待导入 {len(rows)-len(answers)} 道。', '',
             '> 图片使用 images/ 相对路径，移动文档时请带上整个导出文件夹。', '']
    assignment = None
    for row in rows:
        if row['assignment'] != assignment:
            assignment = row['assignment']
            lines += [f'## 作业 {assignment}：{row["title"]}', '']
        question = row['question']
        lines += [f'### 第 {question["number"]} 题 · {question["type"]}'
                  f'（总第 {row["order"]} 题）', '', question['stem'], '']
        for option in question.get('options', []):
            lines += [f'- **{option["label"]}.** {option["text"]}', '']
        if question.get('blank_count'):
            lines += [f'填空数量：{question["blank_count"]}。', '']
        if question.get('displayed_type'):
            lines += [f'> 原页面标注：{question["displayed_type"]}。', '']
        lines += ['#### AI 解答', '', answer_headings(answers[row['key']])
                  if row['key'] in answers else '*尚未导入解答。*', '']
    if report.get('warnings'):
        lines += ['## 原题导出提示', '']+[f'- {warning}' for warning in report['warnings']]+['']
    return '\n'.join(lines)


def atomic_write(path, content):
    temporary = path.with_name('.answers-'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(content, encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def merge_answers(source, text, exclude_range=None, exclude_orders=None):
    source, report, rows, document_id = load_questions(source)
    keys = {row['key'] for row in rows}
    incoming = parse_answers(text, keys)
    protected = set()
    if exclude_range is not None:
        first, last = exclude_range
        if not 1 <= first <= last <= len(rows):
            raise ValueError('受保护的题目范围无效')
        protected.update(row['key'] for row in rows[first-1:last])
    if exclude_orders is not None:
        if any(type(order) is not int or not 1 <= order <= len(rows) for order in exclude_orders):
            raise ValueError('受保护的题号无效')
        protected.update(rows[order-1]['key'] for order in exclude_orders)
    skipped_protected = bool(incoming.keys() & protected)
    incoming = {key: answer for key, answer in incoming.items() if key not in protected}
    store = load_store(source.parent, document_id, keys)
    if incoming:
        store['answers'].update(incoming)
        store['updated_at'] = datetime.now(timezone.utc).isoformat()
        # JSON is authoritative: if writing Markdown fails, a retry rebuilds it.
        atomic_write(source.parent/'answers.json', json.dumps(store, ensure_ascii=False, indent=2)+'\n')
        atomic_write(source.parent/'questions_with_answers.md', build_document(report, rows, store['answers']))
    document = source.parent/'questions_with_answers.md'
    return {'status': 'merged' if incoming else 'protected' if skipped_protected else 'unrecognized',
            'updated': [r['order'] for r in rows if r['key'] in incoming],
            'answered': [r['order'] for r in rows if r['key'] in store['answers']],
            'answered_count': len(store['answers']), 'question_count': len(rows),
            'document': str(document) if document.is_file() else None}


def main():
    parser = argparse.ArgumentParser(description='生成可分题的 AI 提示词，合并粘贴的 Markdown 解答。')
    sub = parser.add_subparsers(dest='command', required=True)
    prompt = sub.add_parser('prompt', help='生成指定连续题号范围的提示词')
    prompt.add_argument('source', type=Path, help='导出文件夹或 questions.json')
    prompt.add_argument('--first', type=int, default=1)
    prompt.add_argument('--last', type=int)
    prompt.add_argument('--json', action='store_true', help='供 GUI 读取的 JSON 输出')
    add_prompt_arguments(prompt)
    merge = sub.add_parser('merge', help='按标识累计导入解答并生成整理文档')
    merge.add_argument('source', type=Path)
    merge.add_argument('--input', type=Path, required=True, help='UTF-8 Markdown 回复文件')
    merge.add_argument('--exclude-range', type=int, nargs=2, metavar=('FIRST', 'LAST'),
                       help='合并其他题组时跳过正在由 API 解答的题号范围')
    merge.add_argument('--exclude-orders', type=int, nargs='+', help='保留这些题号的已有答案')
    args = parser.parse_args()
    try:
        if args.command == 'prompt':
            value = make_prompt(args.source, args.first, args.last, resolve_prompt(args))
            print(json.dumps({'prompt': value}, ensure_ascii=False) if args.json else value)
        else:
            print(json.dumps(merge_answers(args.source, args.input.read_text(encoding='utf-8-sig'),
                                           args.exclude_range, args.exclude_orders),
                             ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'失败：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
