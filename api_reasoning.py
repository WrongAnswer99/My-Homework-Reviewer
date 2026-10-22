"""Reasoning metadata discovery with an explicit, opt-in minimal API probe."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from api_config import DEFAULT_CONFIG, endpoint_url, load_profiles, normalize_profile
from api_homework import cloudflare_block_message
from api_transport import APIConnectionError, APIStatusError, HTTPError, create_client

sys.dont_write_bytecode = True
EFFORTS = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')


def catalog_urls(base_url, model):
    endpoint = urlsplit(endpoint_url(base_url))
    path = endpoint.path.removesuffix('/chat/completions')
    yield urlunsplit((endpoint.scheme, endpoint.netloc, path+'/models', '', ''))
    # Model Studio publishes rich metadata at a separate endpoint on the same
    # host. Its OpenAI-compatible /models may contain only basic identifiers.
    if (path.endswith('/compatible-mode/v1') and
            (endpoint.hostname or '').endswith('.aliyuncs.com')):
        yield urlunsplit((endpoint.scheme, endpoint.netloc, '/api/v1/models',
                          urlencode({'model': model, 'page_size': 1}), ''))


def parse_model_catalog(document, model):
    """Report only fields actually supplied by the model-list response."""
    if not isinstance(document, dict):
        return None
    rows = document.get('data')
    if rows is None and isinstance(document.get('output'), dict):
        rows = document['output'].get('models')
    if not isinstance(rows, list):
        return None
    row = next((item for item in rows if isinstance(item, dict) and
                (item.get('id') == model or item.get('model') == model)), None)
    if row is None:
        return None
    reasoning = row.get('reasoning')
    efforts = None
    parameter = 'reasoning_effort'
    if isinstance(reasoning, dict):
        efforts = reasoning.get('supported_efforts')
        if isinstance(efforts, list):
            parameter = 'reasoning'
    if efforts is None:
        efforts = row.get('supported_reasoning_efforts')
    if efforts is None and isinstance(row.get('reasoning_effort'), dict):
        efforts = row['reasoning_effort'].get('values')
    if isinstance(efforts, list):
        levels = [level for level in EFFORTS if level in efforts]
        if levels:
            return {'status': 'levels', 'levels': levels, 'parameter': parameter,
                    'message': f'模型目录提供 {len(levels)} 个思考档位'}
    params = row.get('supported_parameters')
    if isinstance(params, list):
        if 'reasoning' in params:
            parameter = 'reasoning'
        if 'reasoning' in params or 'reasoning_effort' in params:
            return {'status': 'supported', 'levels': [], 'parameter': parameter,
                    'message': '模型目录确认有思考参数，但未提供具体档位'}
        return {'status': 'unknown', 'levels': [], 'parameter': parameter,
                'message': '模型目录未列出可用的思考强度；可手动选择并以请求结果为准'}
    capabilities = row.get('capabilities')
    if isinstance(capabilities, list) and 'Reasoning' in capabilities:
        return {'status': 'supported', 'levels': [], 'parameter': parameter,
                'message': '模型目录标注支持思考，但未提供具体档位'}
    return {'status': 'unknown', 'levels': [], 'parameter': parameter,
            'message': '模型目录只有基本信息，未提供思考强度；可手动选择'}


def parse_probe_error(document, model, parameter='reasoning_effort'):
    """Trust model-specific validation only, never a gateway's generic enum."""
    if not isinstance(document, dict):
        return None
    error = document.get('error', document)
    message = error.get('message') if isinstance(error, dict) else None
    if not isinstance(message, str) or model not in message or 'reasoning' not in message.lower():
        return None
    match = re.search(r'\b(?:use|supported values are|supported efforts are)\s*:?\s*(.+)',
                      message, re.IGNORECASE)
    if not match:
        return None
    values = re.split(r'[.;\n（(]', match.group(1), maxsplit=1)[0]
    tokens = re.findall(r'[a-z_][a-z_0-9]*', values.lower())
    if not tokens or any(token not in (*EFFORTS, 'and', 'or') for token in tokens):
        return None
    levels = [level for level in EFFORTS if level in tokens]
    if not levels:
        return None
    return {'status': 'levels', 'levels': levels, 'parameter': parameter,
            'message': f'模型专属参数校验提示提供 {len(levels)} 个思考档位；未逐档验证生成效果'}


def probe_reasoning(profile, parameter='reasoning_effort'):
    """One small request without homework; accepted requests may incur charges."""
    extra = {parameter: {'effort': 'none'} if parameter == 'reasoning' else 'none'}
    try:
        with create_client(profile, timeout=min(profile['timeout'], 20)) as client:
            with client.chat.completions.with_streaming_response.create(
                    model=profile['model'], messages=[{'role': 'user', 'content': 'Reply only OK.'}],
                    stream=False, max_tokens=16, extra_body=extra):
                pass
        # A successful request can still mean the gateway silently ignored the
        # parameter. It is not evidence for a list of supported efforts.
        message = '最小探测请求成功，但接口未明确返回档位，不能确认思考参数是否生效；可手动选择'
    except APIStatusError as exc:
        try:
            document = exc.response.json()
            blocked = cloudflare_block_message(document)
            if blocked:
                return {'status': 'blocked', 'levels': [], 'parameter': parameter, 'message': blocked}
            if exc.status_code in {400, 422}:
                result = parse_probe_error(document, profile['model'], parameter)
                if result:
                    return result
        except (ValueError, UnicodeError, OSError):
            pass
        finally:
            exc.response.close()
        message = f'最小探测返回 HTTP {exc.status_code}，未提供模型专属档位；可手动选择'
    except (APIConnectionError, HTTPError, OSError, ValueError) as exc:
        message = f'最小探测未完成（{type(exc).__name__}）；可手动选择或重试'
    return {'status': 'unknown', 'levels': [], 'parameter': parameter, 'message': message}


def discover(profile, *, allow_probe=False):
    profile = normalize_profile(profile, require_ready=True)
    errors = []
    fallback = None
    for url in catalog_urls(profile['base_url'], profile['model']):
        try:
            target = urlsplit(url)
            base = urlunsplit((target.scheme, target.netloc, target.path.removesuffix('/models'), '', ''))
            with create_client(profile, base_url=base, timeout=min(profile['timeout'], 12)) as client:
                with client.models.with_streaming_response.list(extra_query=dict(parse_qsl(target.query))) as response:
                    raw = bytearray()
                    for chunk in response.iter_bytes(chunk_size=65536):
                        raw.extend(chunk)
                        if len(raw) > 8 * 1024 * 1024:
                            raise ValueError('模型目录响应超过 8 MiB')
            result = parse_model_catalog(json.loads(raw.decode('utf-8-sig')), profile['model'])
            if result is None:
                errors.append('模型目录没有找到当前模型 ID')
                continue
            if result['status'] == 'levels' or (result['status'] == 'supported' and not allow_probe):
                return result
            fallback = result
        except APIStatusError as exc:
            try:
                document = exc.response.json()
                block_message = cloudflare_block_message(document)
                if block_message:
                    return {'status': 'blocked', 'levels': [], 'parameter': 'reasoning_effort',
                            'message': block_message}
            except (ValueError, UnicodeError, OSError):
                pass
            finally:
                exc.response.close()
            errors.append(f'模型目录返回 HTTP {exc.status_code}')
        except (APIConnectionError, HTTPError, OSError, ValueError, UnicodeError) as exc:
            errors.append(type(exc).__name__ if isinstance(exc, OSError) else str(exc))
    if allow_probe:
        return probe_reasoning(profile, (fallback or {}).get('parameter', 'reasoning_effort'))
    return fallback or {'status': 'unknown', 'levels': [], 'parameter': 'reasoning_effort',
                        'message': ('；'.join(errors) or '接口未提供模型目录')+'；可手动选择思考强度'}


def main():
    parser = argparse.ArgumentParser(description='查询模型思考档位；默认只读，显式允许后可最小探测。')
    parser.add_argument('--probe', action='store_true', help='目录缺少档位时发送最小测试请求，可能产生费用')
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--profile', required=True)
    args = parser.parse_args()
    try:
        profile = next((item for item in load_profiles(args.config) if item['id'] == args.profile), None)
        if profile is None:
            raise ValueError('API 配置不存在')
        result = discover(profile, allow_probe=args.probe)
        serialized = json.dumps(result, ensure_ascii=False)
        print(serialized.replace(profile['api_key'], '[Key hidden]') if profile['api_key'] else serialized)
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({'status': 'unknown', 'levels': [], 'parameter': 'reasoning_effort',
                          'message': f'获取模型信息失败：{exc}；可手动选择思考强度'}, ensure_ascii=False))
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
