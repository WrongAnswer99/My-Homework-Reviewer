"""Read-only model catalog lookup; credentials arrive through stdin, not argv."""
from __future__ import annotations

import json
import sys

from api_config import normalize_profile
from api_homework import cloudflare_block_message
from api_transport import APIConnectionError, APIStatusError, HTTPError, create_client


def list_models(profile):
    profile = normalize_profile(profile)
    if not profile['base_url']:
        raise ValueError('请先填写 Base URL')
    try:
        with create_client(profile, timeout=min(profile['timeout'], 20)) as client:
            with client.models.with_streaming_response.list() as response:
                raw = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    raw.extend(chunk)
                    if len(raw) > 8 * 1024 * 1024:
                        raise ValueError('模型列表响应超过 8 MiB')
        document = json.loads(raw.decode('utf-8-sig'))
        rows = document.get('data') if isinstance(document, dict) else None
        if not isinstance(rows, list):
            raise ValueError('接口未返回有效的模型列表')
        models = list(dict.fromkeys(row['id'].strip() for row in rows
                                   if isinstance(row, dict) and isinstance(row.get('id'), str)
                                   and row['id'].strip()))
        return {'ok': True, 'models': models,
                'message': f'已获取 {len(models)} 个模型' if models else '接口返回的模型列表为空；可使用自定义模型id'}
    except APIStatusError as exc:
        detail = ''
        try:
            detail = cloudflare_block_message(exc.response.json()) or ''
        except (ValueError, UnicodeError, OSError):
            pass
        finally:
            exc.response.close()
        raise ValueError(detail or f'获取模型列表失败：HTTP {exc.status_code}；请检查地址、Key 或服务商是否支持模型列表') from None
    except (APIConnectionError, HTTPError, OSError):
        raise ValueError('获取模型列表失败：网络连接失败或超时，请检查网络与接口地址') from None


def main():
    profile = {}
    try:
        profile = json.load(sys.stdin)
        result = list_models(profile)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        result = {'ok': False, 'models': [], 'message': str(exc)}
    serialized = json.dumps(result, ensure_ascii=False)
    key = profile.get('api_key') if isinstance(profile, dict) else None
    print(serialized.replace(key, '[Key hidden]') if isinstance(key, str) and key else serialized)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
