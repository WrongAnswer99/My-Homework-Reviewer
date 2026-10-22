"""Plan sequential card requests using existing saved answers, without a cache."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from answers_homework import load_questions, load_store
from review_homework import load_review


def plan_cards(source, manifest):
    source, report, rows, document_id = load_questions(source)
    manifest = Path(manifest).resolve()
    data = json.loads(manifest.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or data.get('source_file') != report['source_file']:
        raise ValueError('图片清单与当前作业不匹配，请重新生成图片')
    keys = {row['key'] for row in rows}
    answers = load_store(source.parent, document_id, keys)['answers']
    review = load_review(source)[3]['questions']
    answered = set()
    for row in rows:
        record = review.get(row['key'], {})
        if not isinstance(record, dict) or not isinstance(record.get('corrected_answer', ''), str):
            raise ValueError('修正答案记录格式无效，请检查 review.json')
        if row['key'] in answers or record.get('corrected_answer', '').strip():
            answered.add(row['order'])
    pages = data.get('pages')
    if not isinstance(pages, list) or not pages:
        raise ValueError('没有可调用的题目图片，请先生成图片')
    cards, expected = [], 1
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get('image'), str):
            raise ValueError('图片清单格式无效，请重新生成图片')
        first, last = page.get('first'), page.get('last')
        if (type(first) is not int or type(last) is not int
                or first != expected or not first <= last <= len(rows)):
            raise ValueError('图片题号必须连续覆盖整份作业，请重新生成图片')
        image = (manifest.parent/page['image']).resolve()
        if not image.is_relative_to(manifest.parent) or not image.is_file():
            raise ValueError('题目图片缺失或路径无效，请重新生成图片')
        if image.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp', '.gif'}:
            raise ValueError('题目图片格式不支持，请重新生成图片')
        keep = sorted(answered.intersection(range(first, last+1)))
        cards.append({'first': first, 'last': last, 'image': str(image),
                      'keep_orders': keep, 'complete': len(keep) == last-first+1})
        expected = last+1
    if expected != len(rows)+1:
        raise ValueError('图片没有覆盖全部题目，请重新生成图片')
    return {'cards': cards, 'answered': sorted(answered), 'question_count': len(rows)}


def main():
    parser = argparse.ArgumentParser(description='检查全部图片卡片及已保存解答，生成依次调用计划。')
    parser.add_argument('source', type=Path)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(plan_cards(args.source, args.manifest), ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'失败：{exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
