"""Explicit live SDK probe; never run by unittest discovery.

Reads the existing local API configuration rather than accepting a Key on the
command line. Run: python -X utf8 tests/probe_api_sdk.py --protocol all
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if (ROOT/'data/sdk_packages').is_dir():
    sys.path.insert(0, str(ROOT/'data/sdk_packages'))

from api_config import DEFAULT_CONFIG, endpoint_url, load_profiles


def main():
    parser = argparse.ArgumentParser(description='Run the provider-published OpenAI SDK examples.')
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--profile', help='Saved profile ID; defaults to the UI selection.')
    parser.add_argument('--protocol', choices=('chat', 'responses', 'models', 'all'), default='all')
    parser.add_argument('--prompt', default='Explain quantum entanglement in one paragraph.')
    parser.add_argument('--timeout', type=float, default=60)
    args = parser.parse_args()
    try:
        from openai import OpenAI
    except ImportError:
        parser.error('Install openai with: python -m pip install --target data/sdk_packages "openai>=2,<3"')
    profiles = load_profiles(args.config)
    selected = args.profile
    if not selected:
        try:
            selected = json.loads((ROOT/'data/gui_state.json').read_text(encoding='utf-8')).get('selected_api')
        except (OSError, ValueError):
            pass
    profile = next((p for p in profiles if p['id'] == selected), profiles[0] if len(profiles) == 1 else None)
    if profile is None:
        parser.error('Select a saved API profile with --profile.')
    endpoint = urlsplit(endpoint_url(profile['base_url']))
    base_url = urlunsplit((endpoint.scheme, endpoint.netloc,
                          endpoint.path.removesuffix('/chat/completions'), '', ''))
    results = []

    def report(protocol, operation):
        try:
            value = operation()
            result = {'protocol': protocol, 'ok': True, **value}
        except Exception as exc:
            result = {'protocol': protocol, 'ok': False, 'error_type': type(exc).__name__,
                      'http_status': getattr(exc, 'status_code', None), 'message': str(exc)[:4000]}
        serialized = json.dumps(result, ensure_ascii=False)
        if profile['api_key']:
            serialized = serialized.replace(profile['api_key'], '[Key hidden]')
        print(serialized, flush=True)
        results.append(result['ok'])

    with OpenAI(base_url=base_url, api_key=profile['api_key'],
                max_retries=0, timeout=args.timeout) as client:
        if args.protocol in {'chat', 'all'}:
            def chat():
                completion = client.chat.completions.create(
                    model=profile['model'], messages=[{'role': 'user', 'content': args.prompt}])
                return {'text': completion.choices[0].message.content,
                        'finish_reason': completion.choices[0].finish_reason}
            report('chat', chat)
        if args.protocol in {'responses', 'all'}:
            def responses():
                response = client.responses.create(model=profile['model'], input=args.prompt)
                return {'text': response.output_text, 'status': response.status}
            report('responses', responses)
        if args.protocol in {'models', 'all'}:
            def models():
                catalog = client.models.list()
                row = next((item for item in catalog.data if item.id == profile['model']), None)
                if row is None:
                    return {'model_found': False, 'model_count': len(catalog.data)}
                return {'model_found': True, 'metadata': row.model_dump()}
            report('models', models)
    return 0 if all(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
