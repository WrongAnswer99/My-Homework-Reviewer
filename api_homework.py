"""Request answers for one rendered card through a compatible API CLI."""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import re
import sys
from http.client import HTTPException
from pathlib import Path

from answers_homework import load_questions, make_prompt, parse_answers
from api_config import DEFAULT_CONFIG, load_profiles, normalize_profile
from prompt_config import add_prompt_arguments, resolve_prompt
from api_transport import APIConnectionError, APIStatusError, APITimeoutError, HTTPError, TimeoutException, create_client

sys.dont_write_bytecode = True
API_PREFIX = '@@API '


def cloudflare_block_message(document):
    if (not isinstance(document, dict) or document.get('cloudflare_error') is not True or
            document.get('error_code') != 1010):
        return None
    ray = document.get('ray_id')
    reference = f'；Ray ID {ray}' if isinstance(ray, str) and re.fullmatch(r'[0-9a-fA-F]{8,32}', ray) else ''
    return ('服务商的 Cloudflare 安全策略拒绝了本程序客户端（错误 1010）'
            f'{reference}；请联系服务商允许此客户端访问')


def build_payload(profile, source, first, last, image=None, reasoning_effort=None,
                  reasoning_parameter='reasoning_effort', prompt_override=None, prompt_text=None):
    profile = normalize_profile(profile, require_ready=True)
    _, _, rows, _ = load_questions(source)
    prompt = prompt_override if prompt_override is not None else make_prompt(source, first, last, prompt_text)
    if not profile['send_image']:
        prompt = prompt.replace('我随后发送的这张电路、模拟电子技术或数字电子技术题目图片',
                                '下方的电路、模拟电子技术或数字电子技术题目文字')
        prompt = prompt.replace('我随后发送的数字电子技术题目图片', '下方的数字电子技术题目文字')
        prompt += '\n本次没有发送题图；题干中的本地图片链接无法访问，缺少图中条件时请明确说明，不能猜测。\n'
    text = []
    for row in rows[first-1:last]:
        question = row['question']
        text += [f'### 总第 {row["order"]} 题 · {question["type"]}', question['stem']]
        text += [f'{option["label"]}. {option["text"]}' for option in question.get('options', [])]
        if question.get('blank_count'):
            text.append(f'填空数量：{question["blank_count"]}')
    message_text = (prompt if prompt_override is not None else
                    prompt+'\n题目文字（图中文字以题图为准）：\n'+'\n\n'.join(text))
    content = [{'type': 'text', 'text': message_text}]
    if profile['send_image']:
        if image is None:
            raise ValueError('请先生成图片并选中要解答的卡片')
        image = Path(image)
        mime = mimetypes.guess_type(image.name)[0]
        if mime not in {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}:
            raise ValueError('题图格式不受支持')
        encoded = base64.b64encode(image.read_bytes()).decode('ascii')
        content.append({'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{encoded}'}})
    messages = []
    if profile['system_prompt'].strip():
        messages.append({'role': 'system', 'content': profile['system_prompt']})
    messages.append({'role': 'user', 'content': content if profile['send_image'] else content[0]['text']})
    payload = {'model': profile['model'], 'messages': messages, 'stream': profile['stream'],
               'temperature': profile['temperature'], 'top_p': profile['top_p']}
    if profile['max_tokens']:
        payload['max_tokens'] = profile['max_tokens']
    payload.update(profile['extra_body'])
    if reasoning_effort is not None:
        if reasoning_effort not in {'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'}:
            raise ValueError('思考强度必须是有效的模型档位')
        if reasoning_parameter not in {'reasoning_effort', 'reasoning'}:
            raise ValueError('不支持的思考参数类型')
        # A per-request choice takes precedence over saved provider defaults.
        # Qwen 3.8 rejects reasoning_effort together with thinking_budget.
        for key in ('enable_thinking', 'thinking_budget', 'reasoning_effort'):
            payload.pop(key, None)
        payload.pop('reasoning', None)
        if reasoning_parameter == 'reasoning':
            payload['reasoning'] = {'effort': reasoning_effort}
        else:
            payload['reasoning_effort'] = reasoning_effort
    return payload, {row['key'] for row in rows[first-1:last]}


def content_text(content):
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ''.join(part.get('text', '') for part in content
                       if isinstance(part, dict) and isinstance(part.get('text'), str))
    raise ValueError('API 返回的内容格式无法识别')


def check_finish(reason):
    if reason in {'length', 'max_tokens'}:
        raise ValueError('解答达到输出长度上限，未导入；请增大最大输出 Token 或减少每页题数')
    if reason in {'content_filter', 'tool_calls', 'function_call'}:
        raise ValueError(f'API 未返回正常解答（{reason}），未导入')


def stream_answers(response, on_delta, on_reasoning=lambda count: None):
    parts, finished = [], False
    event = []
    reasoning_chars = 0

    def dispatch(lines):
        nonlocal finished, reasoning_chars
        data = '\n'.join(lines)
        if not data:
            return False
        if data.strip() == '[DONE]':
            finished = True
            return True
        obj = json.loads(data)
        if obj.get('error'):
            error = obj['error']
            raise ValueError(error.get('message', 'API 流式返回错误') if isinstance(error, dict) else str(error))
        choices = obj.get('choices') or []
        if not choices:  # Usage-only chunks and heartbeats carry no answer.
            return False
        choice = next((item for item in choices if item.get('index', 0) == 0), None)
        if choice is None:
            return False
        delta = choice.get('delta') or {}
        thinking = delta.get('reasoning_content')
        if isinstance(thinking, str) and thinking:
            reasoning_chars += len(thinking)
            on_reasoning(reasoning_chars)
        text = content_text(delta.get('content'))
        if text:
            parts.append(text)
            on_delta(text)
        reason = choice.get('finish_reason')
        if reason is not None:
            check_finish(reason)
            finished = True
        return False

    for raw in response:
        line = (raw.decode('utf-8-sig') if isinstance(raw, bytes) else raw.lstrip('\ufeff')).rstrip('\r\n')
        if not line:
            if dispatch(event):
                break
            event = []
        elif line.startswith('data:'):
            event.append(line[5:].lstrip(' '))
    else:
        if event:
            dispatch(event)
    if not finished:
        raise ValueError('API 连接在完成前中断，未导入半截解答')
    return ''.join(parts)


def request_answers(profile, payload, on_delta=lambda text: None,
                    on_reasoning=lambda count: None):
    extra = {key: value for key, value in payload.items() if key not in {'model', 'messages', 'stream'}}
    try:
        with create_client(profile) as client, client.chat.completions.with_streaming_response.create(
                model=payload['model'], messages=payload['messages'], stream=payload['stream'],
                extra_body=extra) as response:
            if 'text/event-stream' in response.headers.get('Content-Type', '').lower():
                text = stream_answers(response.iter_lines(), on_delta, on_reasoning)
                if not text.strip():
                    raise ValueError('API 返回的解答为空')
                return text
            obj = json.loads(response.read().decode('utf-8-sig'))
            if obj.get('error'):
                error = obj['error']
                raise ValueError(error.get('message', 'API 返回错误') if isinstance(error, dict) else str(error))
            choices = obj.get('choices') or []
            if not choices:
                raise ValueError('API 没有返回解答；请检查接口地址及模型配置')
            choice = choices[0]
            check_finish(choice.get('finish_reason'))
            text = content_text(choice.get('message', {}).get('content'))
            if not text.strip():
                raise ValueError('API 返回的解答为空')
            on_delta(text)
            return text
    except APIStatusError as exc:
        detail = ''
        blocked = False
        try:
            obj = exc.response.json()
            block_message = cloudflare_block_message(obj)
            if block_message:
                detail = '：'+block_message
                blocked = True
            else:
                error = obj.get('error', obj)
                if isinstance(error, dict) and isinstance(error.get('message'), str):
                    detail = '：'+error['message']
        except (ValueError, UnicodeError, AttributeError, OSError, HTTPException):
            pass
        finally:
            exc.response.close()
        if blocked:
            raise ValueError(f'HTTP {exc.status_code}{detail}') from None
        raise ValueError(f'HTTP {exc.status_code}{detail}；请检查地址、Key、模型和额度') from None
    except (APITimeoutError, TimeoutException, TimeoutError):
        raise ValueError('API 连接或读取超时；可在配置中调整超时秒数') from None
    except APIConnectionError:
        raise ValueError('无法连接 API；请检查网络、代理及接口地址') from None
    except HTTPError:
        raise ValueError('API 连接在接收解答时中断，未导入半截解答') from None


def validate_answer(text, expected_keys):
    answers = parse_answers(text, expected_keys)
    if set(answers) != expected_keys:
        raise ValueError('API 解答缺少当前卡片的完整题目标记，未自动导入；请检查解答或重新调用')


def emit(event):
    print(API_PREFIX+json.dumps(event, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description='发送当前题目卡片到兼容 API，返回可整理的 Markdown 解答。')
    parser.add_argument('source', type=Path)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--profile', required=True, help='本地 API 配置 ID；不在命令行传递 Key')
    parser.add_argument('--first', type=int, required=True)
    parser.add_argument('--last', type=int, required=True)
    parser.add_argument('--image', type=Path)
    parser.add_argument('--reasoning-effort', choices=('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'),
                        help='覆盖本次请求的思考强度；不传时使用模型配置或服务端默认值')
    parser.add_argument('--reasoning-parameter', choices=('reasoning_effort', 'reasoning'),
                        default='reasoning_effort')
    add_prompt_arguments(parser)
    args = parser.parse_args()
    profile = None
    try:
        profile = next((p for p in load_profiles(args.config) if p['id'] == args.profile), None)
        if profile is None:
            raise ValueError('所选 API 配置不存在，请重新选择')
        profile = normalize_profile(profile, require_ready=True)
        emit({'type': 'status', 'message': f'正在准备第 {args.first}–{args.last} 题…'})
        payload, keys = build_payload(profile, args.source, args.first, args.last,
                                      args.image, args.reasoning_effort, args.reasoning_parameter,
                                      prompt_text=resolve_prompt(args))
        emit({'type': 'status', 'message': f'正在请求 {profile["name"]}，等待返回解答…'})
        text = request_answers(profile, payload,
                               lambda value: emit({'type': 'delta', 'text': value}),
                               lambda count: emit({'type': 'reasoning', 'count': count}))
        try:
            validate_answer(text, keys)
        except ValueError as exc:
            emit({'type': 'result', 'text': text, 'valid': False, 'message': str(exc)})
            return 1
        emit({'type': 'result', 'text': text, 'valid': True})
        return 0
    except (OSError, HTTPException, ValueError, TypeError, KeyError, AttributeError) as exc:
        message = str(exc)
        if profile and profile.get('api_key'):
            message = message.replace(profile['api_key'], '[Key 已隐藏]')
        emit({'type': 'error', 'message': message})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
