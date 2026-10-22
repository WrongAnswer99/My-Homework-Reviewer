"""Question review, favorites and Markdown copying CLI."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.dont_write_bytecode = True

from answers_homework import atomic_write, load_questions, load_store

IMAGE = re.compile(r'!\[[^\n]*?\]\(([^\n)]+)\)')
CONCLUSION = re.compile(r'^\s*(?:#{1,6}\s*)?(?:(?:\*\*|__)\s*结论\s*(?:\*\*|__)|结论)\s*(?:[:：]\s*(.*))?\s*$')


def answer_conclusion(answer):
    lines = answer.splitlines()
    for index, line in enumerate(lines):
        match = CONCLUSION.fullmatch(line)
        if match is None:
            continue
        candidates = [match.group(1)] if match.group(1) else lines[index+1:]
        for candidate in candidates:
            candidate = candidate.strip()
            if not candidate:
                continue
            if candidate.startswith(('```', '#')) or re.fullmatch(
                    r'(?:\*\*|__)?(?:推导过程|检查|分析|解题过程)(?:\*\*|__)?', candidate):
                return ''
            candidate = re.sub(r'^[-*]\s+', '', candidate)
            candidate = re.sub(r'!\[[^]]*\]\([^)]*\)', '[图片]', candidate)
            candidate = re.sub(r'\[([^]]+)\]\([^)]*\)', r'\1', candidate)
            return ' '.join(candidate.replace('**', '').replace('__', '').replace('`', '').split())
        return ''
    return ''


def load_review(source):
    source, report, rows, document_id = load_questions(source)
    path = source.parent/'review.json'
    store = {'version': 1, 'document_id': document_id, 'questions': {}}
    if path.exists():
        store = json.loads(path.read_text(encoding='utf-8'))
        if store.get('version') != 1 or store.get('document_id') != document_id:
            raise ValueError('复习记录与当前题目不匹配，未覆盖')
        if not isinstance(store.get('questions'), dict):
            raise ValueError('复习记录格式损坏')
    return source, report, rows, store


def choose(rows, order):
    if not 1 <= order <= len(rows):
        raise ValueError('题目序号超出范围')
    return rows[order-1]


def question_text(row):
    q = row['question']
    lines = [f'## 第 {row["order"]} 题 · {q["type"]}', '',
             f'> 作业 {row["assignment"]}：{row["title"]} · 原题号 {q["number"]}', '', q['stem'], '']
    for option in q.get('options', []):
        lines += [f'- **{option["label"]}.** {option["text"]}', '']
    if q.get('blank_count'):
        lines += [f'填空数量：{q["blank_count"]}。', '']
    return '\n'.join(lines)


def question_detail(source, order):
    source, report, rows, store = load_review(source)
    row = choose(rows, order)
    record = store['questions'].get(row['key'], {})
    answers = load_store(source.parent, store['document_id'], {r['key'] for r in rows})['answers']
    prefix = f'{row["title"]} 第 {row["question"]["number"]} 题：'
    notices = [w for w in report.get('warnings', []) if w.startswith(prefix)]
    text = question_text(row)
    if notices:
        text += '\n'+ '\n'.join('> 导出提示：'+n for n in notices)+'\n'
    return {'key': row['key'], 'order': order, 'text': text,
            'answer': answers.get(row['key'], ''),
            'correction_note': record.get('correction_note', ''),
            'corrected_answer': record.get('corrected_answer', ''),
            'favorite': bool(record.get('favorite'))}


def index_questions(source):
    source, _, rows, store = load_review(source)
    answers = load_store(source.parent, store['document_id'], {r['key'] for r in rows})['answers']
    return {'questions': [{'key': r['key'], 'order': r['order'], 'title': r['title'],
                           'number': r['question']['number'], 'type': r['question']['type'],
                           'conclusion': answer_conclusion(
                               store['questions'].get(r['key'], {}).get('corrected_answer', '')
                               or answers.get(r['key'], '')),
                           'favorite': bool(store['questions'].get(r['key'], {}).get('favorite'))}
                          for r in rows]}


def set_favorite(source, order, value):
    source, _, rows, store = load_review(source)
    row = choose(rows, order)
    record = store['questions'].setdefault(row['key'], {})
    record['favorite'] = value
    atomic_write(source.parent/'review.json', json.dumps(store, ensure_ascii=False, indent=2)+'\n')
    return {'key': row['key'], 'order': order, 'favorite': value}


def save_correction(source, order, note, answer):
    if not isinstance(note, str) or not isinstance(answer, str):
        raise ValueError('纠错信息和修正答案必须是文字')
    source, _, rows, store = load_review(source)
    row = choose(rows, order)
    record = store['questions'].setdefault(row['key'], {})
    record['correction_note'] = note
    record['corrected_answer'] = answer
    atomic_write(source.parent/'review.json', json.dumps(store, ensure_ascii=False, indent=2)+'\n')
    return {'key': row['key'], 'order': order, 'correction_note': note,
            'corrected_answer': answer, 'conclusion': answer_conclusion(answer)}


def copy_markdown(source, order):
    source, _, _, _ = load_review(source)
    detail = question_detail(source, order)
    text = detail['text']
    corrected = detail['corrected_answer']
    answer = corrected if corrected.strip() else detail['answer']
    if answer:
        heading = '修正答案' if corrected.strip() else '已导入解答'
        text += f'\n\n### {heading}\n\n{answer}\n'
    # Clipboard text has no document base directory, so use explicit local URLs.
    def absolute_image(match):
        src = urlsplit(match[1].strip().strip('<>'))
        path = (source.parent/unquote(src.path)).resolve()
        if src.scheme or src.netloc or not path.is_relative_to(source.parent):
            return match[0]
        return match[0].replace(match[1], path.as_uri())
    text = IMAGE.sub(absolute_image, text)
    return {'key': detail['key'], 'order': order, 'text': text,
             'has_images': bool(IMAGE.search(text)),
             'has_answer': bool(answer)}


def main():
    parser = argparse.ArgumentParser(description='复习题目、收藏和复制 Markdown。')
    parser.add_argument('command', choices=['list', 'question', 'favorite', 'copy'])
    parser.add_argument('source', type=Path)
    parser.add_argument('--order', type=int, default=1)
    parser.add_argument('--value', choices=['true', 'false'])
    args = parser.parse_args()
    try:
        if args.command == 'list':
            result = index_questions(args.source)
        elif args.command == 'question':
            result = question_detail(args.source, args.order)
        elif args.command == 'copy':
            result = copy_markdown(args.source, args.order)
        else:
            if args.value is None:
                raise ValueError('收藏操作需要 --value true/false')
            result = set_favorite(args.source, args.order, args.value == 'true')
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        print(json.dumps({'type': 'error', 'message': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

