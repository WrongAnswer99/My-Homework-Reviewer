"""Per-question correction prompt, image, storage and API CLI."""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from http.client import HTTPException
from pathlib import Path

from answers_homework import load_questions, parse_answers
from api_config import DEFAULT_CONFIG, load_profiles, normalize_profile
from api_homework import API_PREFIX, build_payload, emit, request_answers
from render_homework import BrowserRenderer, batch_html, group_questions
from review_homework import question_detail, question_text, save_correction
from prompt_config import FORMULA_GUIDANCE

sys.dont_write_bytecode = True


def correction_prompt(source, order, note, previous_answer):
    _, _, rows, _ = load_questions(source)
    if not 1 <= order <= len(rows):
        raise ValueError('题目序号超出范围')
    if not note.strip():
        raise ValueError('请先输入官方答案和解析')
    row = rows[order-1]
    key = row['key']
    return (f'请为下面这道题生成一份完整、适合复习的综合解析。平台给出的答案和简短解析是官方依据；'
            '最终结论必须以官方答案为准。请对照已有 AI 解答：保留其中正确的推导，明确指出与官方答案或解析冲突的步骤，'
            '结合题干和题图补全官方简析中省略的推导。若题目条件不足或官方解析存在疑点，可以说明疑点，'
            '但不要擅自把最终结论改成旧 AI 答案，也不要编造图中看不到的参数。\n\n'
            '题目：\n'+question_text(row)+'\n'
            '官方答案与解析：\n'+note.strip()+'\n\n'
            '已有 AI 解答（可能有错）：\n'+(previous_answer.strip() or '尚无原解答')+'\n\n'
            +FORMULA_GUIDANCE+'\n\n'
            '只输出这一题的 Markdown，完整保留以下起止标记。按“结论、推导过程、检查”组织内容；'
            '在“结论”中写官方答案，在“推导过程”中给出综合解析并说明旧解答的错误。\n\n'
            f'## 【解答 {key}】\n**结论**\n\n（官方答案）\n\n'
            f'**推导过程**\n\n（综合解析与旧解答纠错）\n\n'
            f'**检查**\n\n（条件和结果核对）\n\n【结束 {key}】\n')


def question_image(source, order, width=1280):
    source, report, rows, _ = load_questions(source)
    if not 1 <= order <= len(rows):
        raise ValueError('题目序号超出范围')
    output = source.parent/'review_images'/f'question_{order:04d}.png'
    if output.is_file():
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    group = group_questions(report, 1)[order-1]
    temporary = output.with_name('.'+uuid.uuid4().hex+'.png')
    try:
        with BrowserRenderer(width) as renderer:
            renderer.capture(batch_html(report, group, source.parent, {}), temporary)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def read_input(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not isinstance(data.get('note'), str) or not isinstance(data.get('answer'), str):
        raise ValueError('纠错输入格式无效')
    return data


def main():
    parser = argparse.ArgumentParser(description='单题纠错与修正解答')
    parser.add_argument('command', choices=('prompt', 'image', 'save', 'api'))
    parser.add_argument('source', type=Path)
    parser.add_argument('--order', type=int, required=True)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--profile')
    parser.add_argument('--reasoning-effort', choices=('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'))
    parser.add_argument('--reasoning-parameter', choices=('reasoning_effort', 'reasoning'),
                        default='reasoning_effort')
    args = parser.parse_args()
    profile = None
    try:
        if args.command == 'image':
            print(json.dumps({'image': str(question_image(args.source, args.order))}, ensure_ascii=False))
            return 0
        if args.input is None:
            raise ValueError('请提供纠错信息')
        values = read_input(args.input)
        if args.command == 'save':
            print(json.dumps(save_correction(args.source, args.order, values['note'], values['answer']),
                             ensure_ascii=False))
            return 0
        detail = question_detail(args.source, args.order)
        prompt = correction_prompt(args.source, args.order, values['note'], detail['answer'])
        if values['answer'].strip():
            prompt += '\n上一版修正答案（仍需核对）：\n'+values['answer'].strip()+'\n'
        if args.command == 'prompt':
            print(json.dumps({'prompt': prompt}, ensure_ascii=False))
            return 0
        profile = next((p for p in load_profiles(args.config) if p['id'] == args.profile), None)
        if profile is None:
            raise ValueError('请先选择 API 配置')
        profile = normalize_profile(profile, require_ready=True)
        image = question_image(args.source, args.order) if profile['send_image'] else None
        emit({'type': 'status', 'message': f'正在请求 {profile["name"]} 修正第 {args.order} 题…'})
        payload, keys = build_payload(profile, args.source, args.order, args.order, image,
                                      args.reasoning_effort, args.reasoning_parameter,
                                      prompt_override=prompt)
        answer = request_answers(profile, payload,
                                 lambda value: emit({'type': 'delta', 'text': value}),
                                 lambda count: emit({'type': 'reasoning', 'count': count}))
        parsed = parse_answers(answer, keys)
        if set(parsed) != keys:
            emit({'type': 'result', 'valid': False, 'text': answer,
                  'message': '纠错回复缺少完整的本题标记，未保存修正答案'})
            return 1
        emit({'type': 'result', 'valid': True, 'answer': parsed[detail['key']]})
        return 0
    except (OSError, HTTPException, ValueError, KeyError, TypeError, AttributeError) as exc:
        message = str(exc)
        if profile and profile.get('api_key'):
            message = message.replace(profile['api_key'], '[Key 已隐藏]')
        if args.command == 'api':
            emit({'type': 'error', 'message': message})
        else:
            print(json.dumps({'message': message}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
