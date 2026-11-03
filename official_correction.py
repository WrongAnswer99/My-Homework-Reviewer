"""Import explicit official grading and feedback from a local result archive."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

from answers_homework import atomic_write
from extract_homework import Archive, DOMParser, Node, Renderer, clean, decode_text
from review_homework import load_review


def without_labels(node):
    return Node(node.tag, dict(node.attrs), [without_labels(child) if isinstance(child, Node) else child
                for child in node.children
                if not isinstance(child, Node) or not child.has_class('answerFont')])


def result_status(node):
    """Include per-blank verdicts; scores alone are not a verdict."""
    boxes = [n for n in node.walk() if n.has_class('myAnswerBx')]
    icons = {name for box in boxes for item in box.walk()
             for name in ('marking_dui', 'marking_cuo', 'marking_bandui') if item.has_class(name)}
    if 'marking_bandui' in icons or {'marking_dui', 'marking_cuo'} <= icons:
        return 'partial'
    if 'marking_cuo' in icons:
        return 'incorrect'
    return 'correct' if 'marking_dui' in icons else None


def option_texts(node, renderer, base):
    options = {}
    for choice in node.walk():
        if choice.has_class('answerBg'):
            label = next((n for n in choice.walk() if n.has_class('num_option')
                          or n.has_class('num_option_dx')), None)
            body = choice.find_class('answer_p')
        elif choice.tag == 'li' and node.find_class('Zy_ulTop') is not None:
            label = next((n for n in choice.children if isinstance(n, Node)
                          and (n.tag == 'i' or n.has_class('num_option')
                               or n.has_class('num_option_dx'))), None)
            body = choice.find_class('after') or next((n for n in choice.children
                                                      if isinstance(n, Node) and n.tag == 'a'), None)
        else:
            continue
        if label is not None and body is not None:
            options[clean(label.text()).rstrip('、.．')] = clean(renderer.render(body, base))
    return options


def comparable(text):
    return re.sub(r'\s+', '', text).replace('\\_', '_')


def remap_answer(answer, node, question, renderer, base):
    """The platform can shuffle choices between attempts; never reuse letters blindly."""
    if not question.get('options'):
        return answer
    letters = re.fullmatch(r'[A-Za-z](?:[A-Za-z\s,，、;；]*[A-Za-z])?', answer.strip())
    if not letters:
        raise ValueError('选择题官方答案不是可识别的选项字母，无法安全匹配')
    original = option_texts(node, renderer, base)
    mapped = []
    for label in re.findall('[A-Za-z]', answer.upper()):
        content = original.get(label)
        matches = [o['label'] for o in question['options']
                   if content is not None and comparable(o['text']) == comparable(content)]
        if len(matches) != 1:
            raise ValueError(f'官方选项 {label} 与当前题目选项无法唯一匹配')
        mapped.append(matches[0])
    return '、'.join(mapped)


def import_results(source, archive_path):
    source, _, rows, store = load_review(source)
    archive_path = Path(archive_path)
    if archive_path.suffix.lower() not in {'.mhtml', '.mht'}:
        raise ValueError('请选择 MHTML/MHT 结果文档')
    archive = Archive(archive_path)
    by_id = {}
    for row in rows:
        qid = str(row['question'].get('id', ''))
        if qid:
            by_id.setdefault(qid, []).append(row)
    notes, unmatched, seen = {}, [], {}
    correct = ungraded = wrong = 0
    with tempfile.TemporaryDirectory(prefix='.official-', dir=source.parent) as temp:
        renderer = Renderer(archive, Path(temp))
        for part in archive.parts:
            if part.get_content_type() != 'text/html':
                continue
            root = DOMParser(decode_text(part)).root
            nodes = [n for n in root.walk() if n.has_class('singleQuesId')]
            if not nodes:
                nodes = [n for n in root.walk() if n.has_class('TiMu')]
            base = str(part.get('Content-Location', ''))
            for node in nodes:
                qid = node.attrs.get('data') or node.attrs.get('id', '')
                status = result_status(node)
                if status is None:
                    ungraded += 1
                    continue
                signature = (status, clean(node.text()), tuple(n.attrs.get('src', '')
                             for n in node.walk() if n.tag == 'img'))
                if qid in seen:
                    if seen[qid] != signature:
                        raise ValueError(f'题目 {qid}：归档存在互相冲突的批阅结果')
                    continue
                seen[qid] = signature
                matches = by_id.get(qid, [])
                if len(matches) != 1:
                    unmatched.append(qid or '无题目 ID')
                    continue
                if status == 'correct':
                    correct += 1
                    continue
                wrong += 1
                row = matches[0]
                renderer.context = f'第 {row["order"]} 题官方纠错'
                box = node.find_class('correctAnswerBx')
                if box is None:
                    raise ValueError(f'{renderer.context}：没有官方正确答案')
                answer = clean(renderer.render(without_labels(box), base))
                if not answer:
                    raise ValueError(f'{renderer.context}：官方正确答案为空')
                mapped = remap_answer(answer, node, row['question'], renderer, base)
                text = '官方正确答案：'+mapped
                if mapped != answer and row['question'].get('options'):
                    text += f'\n\n结果页答案：{answer}（已按当前题目选项对应为 {mapped}）'
                analysis = node.find_class('answerKeyBx')
                if analysis is not None:
                    body = clean(renderer.render(without_labels(analysis), base))
                    if body:
                        text += '\n\n官方简要解析：\n\n'+body
                if row['key'] in notes and notes[row['key']]['official_note'] != text:
                    raise ValueError(f'{renderer.context}：归档存在互相冲突的批阅结果')
                record = store['questions'].setdefault(row['key'], {})
                old = record.get('correction_note', '')
                previous = record.get('official_note', '')
                if previous and previous in old:
                    note = old.replace(previous, text, 1)
                elif text in old:
                    note = old
                else:
                    note = '\n\n'.join(filter(None, [old.strip(), text]))
                record.update(correction_note=note, official_note=text)
                notes[row['key']] = {'key': row['key'], 'order': row['order'], 'note': note,
                                     'answer': record.get('corrected_answer', ''), 'official_note': text}
        if not seen:
            raise ValueError('结果文档中没有明确的批阅判分，请保存已批阅页面')
        if unmatched:
            raise ValueError(f'有 {len(unmatched)} 道已批阅题无法按题目 ID 唯一匹配当前资料，未导入。'
                             '请检查是否选择了对应的原作业。')
        if renderer.warnings:
            raise ValueError('；'.join(dict.fromkeys(renderer.warnings)))
        for relative in renderer.images.values():
            target = source.parent/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copyfile(Path(temp)/relative, target)
        if notes:
            atomic_write(source.parent/'review.json', json.dumps(store, ensure_ascii=False, indent=2)+'\n')
    return {'notes': list(notes.values()), 'imported': len(notes), 'correct': correct,
            'ungraded': ungraded, 'wrong': wrong}


def correction_plan(source):
    _, _, rows, store = load_review(source)
    return {'questions': [dict(key=row['key'], order=row['order'],
                              note=store['questions'][row['key']]['correction_note'],
                              answer=store['questions'][row['key']].get('corrected_answer', ''))
                          for row in rows if store['questions'].get(row['key'], {}).get(
                              'correction_note', '').strip()]}
