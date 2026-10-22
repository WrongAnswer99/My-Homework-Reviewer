import base64
import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

from api_config import endpoint_url, load_profiles, new_profile, normalize_profile, save_profiles
from api_homework import API_PREFIX, build_payload, request_answers, stream_answers, validate_answer
from api_reasoning import discover, parse_model_catalog, parse_probe_error
from api_models import list_models
from answers_homework import load_questions


ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def api_server(reply, content_type='application/json', status=200, headers=None, catalog=None):
    """Local fake service: record wire requests without calling a paid provider."""
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if catalog is None:
                self.send_response(404)
                self.end_headers()
                return
            payload = catalog(self.path) if callable(catalog) else catalog
            code = 200
            if isinstance(payload, tuple):
                code, payload = payload
            payload = payload.encode('utf-8') if isinstance(payload, str) else payload
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            requests.append({'path': self.path, 'body': json.loads(body),
                             'authorization': self.headers.get('Authorization')})
            payload = reply(requests[-1]) if callable(reply) else reply
            payload = payload.encode('utf-8') if isinstance(payload, str) else payload
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            try:
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Cancellation intentionally closes the client connection.
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def fixture(folder):
    report = {'source_file': 'api.mhtml', 'question_count': 2, 'assignment_count': 1,
              'warnings': [], 'assignments': [{'title': 'Circuit', 'questions': [
                  {'id': 'a', 'number': '1', 'type': '单选题', 'stem': '求电流 $I$。',
                   'options': [{'label': 'A', 'text': '1mA'}]},
                  {'id': 'b', 'number': '2', 'type': '判断题', 'stem': '判断工作区。', 'options': []}]}]}
    (folder/'questions.json').write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
    (folder/'questions.md').write_text('原题不覆盖', encoding='utf-8')
    image = folder/'card.png'
    Image.new('RGB', (300, 400), 'white').save(image)
    return image, load_questions(folder)[2]


def answer_blocks(rows):
    return '\n\n'.join(f'## 【解答 {row["key"]}】\n**结论**\n$I=1\\,mA$\n【结束 {row["key"]}】' for row in rows)


def sse(text, finish='stop', done=True):
    chunks = [text[:20], text[20:]]
    events = [': heartbeat\n\n']
    for chunk in chunks:
        events.append('data: '+json.dumps({'choices': [{'index': 0, 'delta': {'content': chunk},
                                                       'finish_reason': None}]}, ensure_ascii=False)+'\n\n')
    if finish is not None:
        events.append('data: '+json.dumps({'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]})+'\n\n')
    events.append('data: '+json.dumps({'choices': [], 'usage': {'total_tokens': 100}})+'\n\n')
    if done:
        events.append('data: [DONE]\n\n')
    return ''.join(events)


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.folder = Path(self.temp.name)
        self.image, self.rows = fixture(self.folder)
        self.profile = new_profile()
        self.profile.update(name='电路模型', model='mock-vision', api_key='TEST-KEY-NOT-A-REAL-SECRET',
                            base_url='http://127.0.0.1:1/v1')

    def tearDown(self):
        self.temp.cleanup()

    def test_config_roundtrip_atomic_validation_and_url_prefixes(self):
        path = self.folder/'api_config.json'
        save_profiles(path, [self.profile])
        self.assertEqual(load_profiles(path), [self.profile])
        self.assertIn(self.profile['api_key'], path.read_text(encoding='utf-8'))
        before = path.read_bytes()
        invalid = dict(self.profile, extra_body={'messages': []})
        with self.assertRaises(ValueError):
            save_profiles(path, [invalid])
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(list(self.folder.glob('.api-config-*')))
        for prefix in ('https://example.invalid/v1', 'https://example.invalid/compatible-mode/v1/'):
            self.assertEqual(endpoint_url(prefix), prefix.rstrip('/')+'/chat/completions')
        url = 'https://example.invalid/compatible-mode/v1/chat/completions/'
        self.assertEqual(endpoint_url(url), url.rstrip('/'))
        for url in ('file:///tmp', 'https://key@example.invalid/v1', 'https://example.invalid/v1?key=x'):
            with self.assertRaises(ValueError):
                endpoint_url(url)

    def test_payload_contains_exact_image_bytes_current_range_and_options(self):
        payload, keys = build_payload(self.profile, self.folder, 1, 1, self.image)
        self.assertEqual(keys, {self.rows[0]['key']})
        content = payload['messages'][-1]['content']
        self.assertIn('A. 1mA', content[0]['text'])
        self.assertNotIn(self.rows[1]['key'], content[0]['text'])
        self.assertEqual(base64.b64decode(content[1]['image_url']['url'].split(',', 1)[1]), self.image.read_bytes())
        text_profile = dict(self.profile, send_image=False, max_tokens=0, extra_body={'enable_thinking': False})
        text_payload, _ = build_payload(text_profile, self.folder, 1, 2)
        self.assertIsInstance(text_payload['messages'][-1]['content'], str)
        self.assertIn('本次没有发送题图', text_payload['messages'][-1]['content'])
        self.assertNotIn('max_tokens', text_payload)
        self.assertFalse(text_payload['enable_thinking'])

    def test_per_request_reasoning_overrides_saved_provider_fields_without_changing_temperature(self):
        profile = dict(self.profile, extra_body={
            'enable_thinking': False, 'thinking_budget': 5000,
            'reasoning_effort': 'low', 'temperature': 0.35})
        for field in ('reasoning_effort', 'reasoning'):
            payload, _ = build_payload(profile, self.folder, 1, 1, self.image, 'high', field)
            self.assertEqual(payload['temperature'], 0.35)
            self.assertNotIn('thinking_budget', payload)
            self.assertNotIn('enable_thinking', payload)
            self.assertEqual(payload[field], 'high' if field == 'reasoning_effort' else {'effort': 'high'})
            self.assertNotIn('reasoning' if field == 'reasoning_effort' else 'reasoning_effort', payload)
        default, _ = build_payload(profile, self.folder, 1, 1, self.image)
        self.assertEqual(default['thinking_budget'], 5000)
        self.assertFalse(default['enable_thinking'])
        self.assertEqual(default['reasoning_effort'], 'low')

    def test_catalog_discovery_only_uses_explicit_metadata(self):
        model = self.profile['model']
        rich = {'data': [{'id': model, 'reasoning': {'supported_efforts': ['low', 'high']},
                          'supported_parameters': ['reasoning']}]}
        basic = {'data': [{'id': model, 'object': 'model', 'owned_by': 'provider'}]}
        self.assertEqual(parse_model_catalog(rich, model)['levels'], ['low', 'high'])
        self.assertEqual(parse_model_catalog(rich, model)['parameter'], 'reasoning')
        self.assertEqual(parse_model_catalog(basic, model)['status'], 'unknown')
        self.assertIsNone(parse_model_catalog(basic, 'other-model'))
        with api_server('{}', catalog=json.dumps(rich)) as (url, requests):
            found = discover(dict(self.profile, base_url=url))
            self.assertEqual(found['levels'], ['low', 'high'])
            self.assertFalse(requests)  # Catalog lookup never creates an answer request.
        with api_server('{}', catalog=json.dumps(basic)) as (url, _):
            found = discover(dict(self.profile, base_url=url))
            self.assertEqual(found['status'], 'unknown')
            self.assertEqual(found['levels'], [])

    def test_probe_error_requires_model_specific_supported_list(self):
        model = self.profile['model']
        specific = {'error': {'message': f'{model} does not support reasoning effort "none"; '
                                         'use low, medium, high, xhigh or max'}}
        self.assertEqual(parse_probe_error(specific, model)['levels'],
                         ['low', 'medium', 'high', 'xhigh', 'max'])
        generic = {'error': {'message': "Invalid value: 'none'. Supported values are: "
                                       "'none', 'minimal', 'low', 'medium', 'high', 'xhigh', and 'max'."}}
        for document in (generic, {}, None, {'error': specific['error']['message']},
                         {'error': {'message': f'{model}: reasoning effort none is not supported'}}):
            self.assertIsNone(parse_probe_error(document, model))

    def test_explicit_probe_discovers_levels_without_sending_homework_or_saved_extras(self):
        model = self.profile['model']
        error = json.dumps({'error': {'message': f'{model} does not support reasoning effort "none"; '
                                                'use low, medium, high, xhigh or max'}})
        for metadata, parameter in (({'id': model}, 'reasoning_effort'),
                                    ({'id': model, 'supported_parameters': ['reasoning']}, 'reasoning')):
            catalog = json.dumps({'data': [metadata]})
            with api_server(error, status=400, catalog=catalog) as (url, requests):
                profile = dict(self.profile, base_url=url, extra_body={'thinking_budget': 10000})
                self.assertIn(discover(profile)['status'], {'unknown', 'supported'})
                self.assertFalse(requests)
                result = discover(profile, allow_probe=True)
                self.assertEqual(result['levels'], ['low', 'medium', 'high', 'xhigh', 'max'])
                self.assertEqual(result['parameter'], parameter)
                self.assertEqual(len(requests), 1)
                body = requests[0]['body']
                self.assertEqual(body['messages'], [{'role': 'user', 'content': 'Reply only OK.'}])
                self.assertEqual(body[parameter], {'effort': 'none'} if parameter == 'reasoning' else 'none')
                self.assertEqual(body['max_tokens'], 16)
                self.assertFalse(body['stream'])
                self.assertNotIn('thinking_budget', body)

    def test_probe_never_invents_levels_from_success_or_generic_validation(self):
        catalog = json.dumps({'data': [{'id': self.profile['model']}]})
        generic = json.dumps({'error': {'message': "Invalid value. Supported values are: "
                                                  "'low', 'medium', 'high'."}})
        for status, reply in ((200, '{}'), (400, generic), (401, generic)):
            with api_server(reply, status=status, catalog=catalog) as (url, requests):
                result = discover(dict(self.profile, base_url=url), allow_probe=True)
                self.assertEqual(result['status'], 'unknown')
                self.assertEqual(result['levels'], [])
                self.assertEqual(len(requests), 1)
        rich = json.dumps({'data': [{'id': self.profile['model'],
                                     'supported_reasoning_efforts': ['low', 'high']}]})
        with api_server('{}', catalog=rich) as (url, requests):
            self.assertEqual(discover(dict(self.profile, base_url=url), allow_probe=True)['levels'], ['low', 'high'])
            self.assertFalse(requests)

    def test_model_list_needs_no_model_and_deduplicates_valid_ids(self):
        catalog = json.dumps({'data': [{'id': 'beta'}, {'id': 'alpha'}, {'id': 'beta'},
                                       {'id': '  trimmed  '}, {'id': ''}, {'id': 123}, None]})
        with api_server('{}', catalog=catalog) as (url, requests):
            result = list_models(dict(self.profile, base_url=url, model=''))
            self.assertTrue(result['ok'])
            self.assertEqual(result['models'], ['beta', 'alpha', 'trimmed'])
            self.assertFalse(requests)
        with api_server('{}', catalog='{"data": []}') as (url, requests):
            self.assertEqual(list_models(dict(self.profile, base_url=url))['models'], [])
            self.assertFalse(requests)
        for catalog in ('{}', '{bad', (401, '{"error":{"message":"secret"}}')):
            with api_server('{}', catalog=catalog) as (url, requests):
                with self.assertRaises(ValueError):
                    list_models(dict(self.profile, base_url=url, model=''))
                self.assertFalse(requests)

    def test_model_list_cli_accepts_credentials_only_over_stdin_and_redacts_errors(self):
        blocked = json.dumps({'cloudflare_error': True, 'error_code': 1010,
                              'ray_id': self.profile['api_key']})
        with api_server('{}', catalog=(403, blocked)) as (url, requests):
            result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(ROOT/'api_models.py')],
                                    input=json.dumps(dict(self.profile, base_url=url, model='')),
                                    capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)['ok'])
            self.assertNotIn(self.profile['api_key'], result.stdout+result.stderr)
            self.assertFalse(requests)

    def test_http_json_stream_and_authorization_use_same_wire_protocol(self):
        answer = answer_blocks(self.rows)
        for streaming in (True, False):
            body = sse(answer) if streaming else json.dumps({'choices': [{'message': {'content': answer}, 'finish_reason': 'stop'}]})
            content_type = 'text/event-stream; charset=utf-8' if streaming else 'application/json'
            with api_server(body, content_type) as (url, requests):
                profile = dict(self.profile, base_url=url, stream=streaming)
                payload, keys = build_payload(profile, self.folder, 1, 2, self.image)
                deltas = []
                result = request_answers(profile, payload, deltas.append)
                self.assertEqual(result, answer)
                self.assertEqual(''.join(deltas), answer)
                validate_answer(result, keys)
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0]['path'], '/v1/chat/completions')
                self.assertEqual(requests[0]['authorization'], 'Bearer '+profile['api_key'])
                self.assertEqual(requests[0]['body'], payload)

    def test_incomplete_truncated_or_foreign_answers_are_not_accepted(self):
        answer = answer_blocks(self.rows)
        for body in (sse(answer, finish=None, done=False), sse(answer, finish='length')):
            with self.assertRaises(ValueError):
                stream_answers(io.BytesIO(body.encode('utf-8')), lambda text: None)
        keys = {row['key'] for row in self.rows}
        for text in (answer_blocks(self.rows[:1]), answer.replace(self.rows[0]['key'], 'H000000000000-Q0001'), ''):
            with self.assertRaises(ValueError):
                validate_answer(text, keys)

    def test_stream_tracks_reasoning_progress_without_inserting_it_into_answer(self):
        body = answer_blocks(self.rows)
        events = [
            {'choices': [{'index': 0, 'delta': {'reasoning_content': '先分析电路'},
                          'finish_reason': None}]},
            {'choices': [{'index': 0, 'delta': {'reasoning_content': '再核对电流'},
                          'finish_reason': None}]},
            {'choices': [{'index': 0, 'delta': {'content': body},
                          'finish_reason': 'stop'}]},
        ]
        stream = ''.join('data: '+json.dumps(item, ensure_ascii=False)+'\n\n' for item in events)
        stream += 'data: [DONE]\n\n'
        reasoning, answer = [], []
        result = stream_answers(io.BytesIO(stream.encode('utf-8')), answer.append, reasoning.append)
        self.assertEqual(result, body)
        self.assertEqual(''.join(answer), body)
        self.assertEqual(reasoning, [5, 10])
        self.assertNotIn('先分析电路', result)

    def test_redirect_is_not_followed_with_authorization(self):
        with api_server(b'{}', status=307, headers={'Location': 'http://127.0.0.1:1/leak'}) as (url, requests):
            profile = dict(self.profile, base_url=url)
            payload, _ = build_payload(profile, self.folder, 1, 2, self.image)
            with self.assertRaisesRegex(ValueError, 'HTTP 307'):
                request_answers(profile, payload)
            self.assertEqual(len(requests), 1)

    def run_cli(self, profile):
        config = self.folder/'api_config.json'
        save_profiles(config, [profile])
        return subprocess.run([sys.executable, '-B', '-X', 'utf8', str(ROOT/'api_homework.py'), str(self.folder),
                               '--config', str(config), '--profile', profile['id'], '--first', '1', '--last', '2',
                               '--image', str(self.image)], capture_output=True, text=True, encoding='utf-8', timeout=15)

    def test_cli_events_and_error_logs_hide_key_without_retrying(self):
        with api_server(sse(answer_blocks(self.rows)), 'text/event-stream') as (url, requests):
            result = self.run_cli(dict(self.profile, base_url=url))
            self.assertEqual(result.returncode, 0, result.stderr)
            events = [json.loads(line[len(API_PREFIX):]) for line in result.stdout.splitlines()]
            self.assertTrue(events[-1]['valid'])
            self.assertEqual(events[-1]['text'], answer_blocks(self.rows))
            self.assertEqual(len(requests), 1)
        error = json.dumps({'error': {'message': 'Rejected '+self.profile['api_key']}})
        with api_server(error, status=401) as (url, requests):
            result = self.run_cli(dict(self.profile, base_url=url))
            self.assertEqual(result.returncode, 1)
            self.assertNotIn(self.profile['api_key'], result.stdout+result.stderr)
            self.assertIn('HTTP 401', result.stdout)
            self.assertEqual(len(requests), 1)

    def test_invalid_complete_reply_is_retained_for_manual_correction(self):
        with api_server(sse(answer_blocks(self.rows[:1])), 'text/event-stream') as (url, _):
            result = self.run_cli(dict(self.profile, base_url=url))
            self.assertEqual(result.returncode, 1)
            event = json.loads(result.stdout.splitlines()[-1][len(API_PREFIX):])
            self.assertFalse(event['valid'])
            self.assertEqual(event['text'], answer_blocks(self.rows[:1]))
            self.assertFalse((self.folder/'answers.json').exists())


if __name__ == '__main__':
    unittest.main()
