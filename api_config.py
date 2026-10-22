"""Plaintext local profiles for compatible Chat Completions services."""
from __future__ import annotations

import json
import math
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


DEFAULT_CONFIG = Path(__file__).resolve().parent/'data/api_config.json'


def new_profile():
    return {'id': uuid.uuid4().hex, 'name': '新 API', 'base_url': '', 'api_key': '',
            'model': '', 'temperature': 0.2, 'top_p': 1.0, 'max_tokens': 8192,
            'timeout': 180, 'stream': True, 'send_image': True,
            'system_prompt': '', 'extra_body': {}}


def endpoint_url(base_url):
    parsed = urlsplit(base_url.strip())
    if (parsed.scheme not in {'https', 'http'} or not parsed.hostname or
            parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('Base URL 必须是完整的 http(s) 地址，不包含账号、查询参数或片段')
    path = parsed.path.rstrip('/')
    if not path.endswith('/chat/completions'):
        path += '/chat/completions'
    return urlunsplit((parsed.scheme, parsed.netloc, path, '', ''))


def normalize_profile(profile, require_ready=False):
    if not isinstance(profile, dict):
        raise ValueError('API 配置必须是对象')
    result = new_profile()
    result.update({key: profile[key] for key in result if key in profile})
    for key in ('id', 'name', 'base_url', 'api_key', 'model', 'system_prompt'):
        if not isinstance(result[key], str):
            raise ValueError(f'{key} 必须是文字')
        if key != 'system_prompt':
            result[key] = result[key].strip()
    if not result['id'] or not result['name']:
        raise ValueError('API 展示名称不能为空')
    if result['base_url']:
        endpoint_url(result['base_url'])
    if '\n' in result['api_key'] or '\r' in result['api_key']:
        raise ValueError('API Key 不能包含换行')
    for key, minimum, maximum in (('temperature', 0, 2), ('top_p', 0.01, 1),
                                   ('max_tokens', 0, 1000000), ('timeout', 5, 3600)):
        value = result[key]
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(value) or not minimum <= value <= maximum):
            raise ValueError(f'{key} 必须在 {minimum}–{maximum} 之间')
        if key in {'max_tokens', 'timeout'} and int(value) != value:
            raise ValueError(f'{key} 必须是整数')
    for key in ('stream', 'send_image'):
        if not isinstance(result[key], bool):
            raise ValueError(f'{key} 必须是布尔值')
    extra = result['extra_body']
    if not isinstance(extra, dict):
        raise ValueError('额外请求参数必须是 JSON 对象')
    if {'messages', 'model', 'stream'} & extra.keys():
        raise ValueError('额外参数不能覆盖 messages、model 或 stream')
    json.dumps(extra, allow_nan=False)
    if require_ready and (not result['base_url'] or not result['model']):
        raise ValueError('请先在 API 配置中填写 Base URL 和模型 ID')
    return result


def load_profiles(path=DEFAULT_CONFIG):
    path = Path(path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('profiles'), list):
        raise ValueError('API 配置文件格式无效')
    profiles = [normalize_profile(item) for item in data['profiles']]
    if len({item['id'] for item in profiles}) != len(profiles):
        raise ValueError('API 配置存在重复 ID')
    return profiles


def save_profiles(path, profiles):
    profiles = [normalize_profile(item) for item in profiles]
    if len({item['id'] for item in profiles}) != len(profiles):
        raise ValueError('API 配置存在重复 ID')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.api-config-'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps({'version': 1, 'profiles': profiles},
                                        ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
