"""Extraction-only comparison. Run with the RAG venv; Cognee runs separately."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')


class BudgetProxy(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, config, folder):
        super().__init__(('127.0.0.1', 0), ProxyHandler)
        self.config, self.folder = config, folder
        ledger = folder/'requests.json'
        self.records = json.loads(ledger.read_text(encoding='utf8')) if ledger.exists() else []
        self.counts = Counter(record['backend'] for record in self.records)
        spent = sum(record.get('seconds', 0) if record.get('status')!='started' else 180 for record in self.records)
        self.lock, self.deadline = threading.Lock(), time.monotonic() + max(0, 600-spent)


class ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, status, data):
        raw = json.dumps(data, ensure_ascii=False).encode('utf8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        phase = self.path.split('/')[1]
        if phase not in {'baseline', 'cognee'} or not self.path.endswith('/chat/completions'):
            self.send_json(404, {'error': {'message': 'Unsupported benchmark endpoint'}})
            return
        with self.server.lock:
            if self.server.counts[phase] >= 2 or time.monotonic() >= self.server.deadline:
                self.send_json(402, {'error': {'message': 'The two-request comparison budget is exhausted.'}})
                return
            self.server.counts[phase] += 1
            number = self.server.counts[phase]
            record = {'backend': phase, 'request': number, 'status': 'started', 'seconds': 0}
            self.server.records.append(record)
            # Reserve the request before outbound I/O; crashes cannot reset the cap.
            write(self.server.folder/'requests.json', self.server.records)
        started = time.monotonic()
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 200000:
                raise ValueError('Request input budget exceeded')
            body = json.loads(self.rfile.read(size))
            body.update(model=self.server.config['model'], stream=False, temperature=0)
            if self.server.config.get('_benchmark_disable_thinking'):
                body['chat_template_kwargs'] = {**body.get('chat_template_kwargs', {}), 'enable_thinking': False}
                body['reasoning_effort'] = 'none'
                record['thinking_requested'] = 'disabled'
            body['max_tokens'] = 32768
            body.pop('max_completion_tokens', None)
            write(self.server.folder / f'{phase}-request-{number}.json', body)
            print(f'{phase}: request {number}/2 started', flush=True)
            import httpx
            seconds = min(180, self.server.deadline-started)
            async def forward():
                async with httpx.AsyncClient(timeout=httpx.Timeout(seconds, connect=10), trust_env=False, follow_redirects=False) as client:
                    return await client.post(self.server.config['base_url'].rstrip('/') + '/chat/completions',
                        headers={'Authorization': 'Bearer ' + self.server.config['api_key']}, json=body)
            response = asyncio.run(asyncio.wait_for(forward(), timeout=seconds))
            payload = response.json()
            record.update(status=response.status_code, usage=payload.get('usage', {}))
            choice = (payload.get('choices') or [{}])[0]
            record['finish_reason'] = choice.get('finish_reason')
            message = choice.get('message') or {}
            record['reasoning_characters'] = len(message.get('reasoning_content') or '')
            record['content_characters'] = len(message.get('content') or '')
            write(self.server.folder / f'{phase}-response-{number}.json', payload)
            self.send_json(response.status_code, payload)
        except Exception as exc:
            record.update(status=502, error=type(exc).__name__)
            self.send_json(502, {'error': {'message': 'Benchmark upstream request failed: ' + type(exc).__name__}})
        finally:
            record['seconds'] = round(time.monotonic() - started, 2)
            with self.server.lock:
                write(self.server.folder / 'requests.json', self.server.records)
            print(f'{phase}: request {number} finished in {record["seconds"]} s', flush=True)


def baseline(config, endpoint, fixture, folder, *, output_name='baseline.json', max_calls=2):
    if max_calls<1:raise RuntimeError('The baseline call budget is exhausted.')
    from openai import OpenAI
    from kb.graph_extraction import graph_payload, truncated_graph_payload
    from kb.knowledge_hierarchy import HIERARCHY_PROMPT, assertion_key, validate_assertions
    from kb.models import Document
    from kb.publication import digest
    document = Document.objects.get(pk=fixture['document_id'])
    if digest(document.md_content) != fixture['source_digest']:
        raise ValueError('The source changed after the comparison fixture was frozen.')
    fetched = {chunk['chunk_id']: chunk for chunk in fixture['chunks']}
    messages = [{'role': 'system', 'content': HIERARCHY_PROMPT},
                {'role': 'user', 'content': json.dumps({key: fixture[key] for key in ('document', 'focus', 'chunks')}, ensure_ascii=False)}]
    rows, raw_rows, rejected, seen = [], [], 0, set()
    client = OpenAI(api_key='benchmark-proxy', base_url=endpoint+'/baseline/v1', max_retries=0, timeout=190)
    complete = False
    for _ in range(max_calls):
        response = client.chat.completions.create(model=config['model'], messages=messages, max_tokens=32768, temperature=0)
        choice = response.choices[0]
        content = choice.message.content or ''
        payload = truncated_graph_payload(content, 'assertions') if choice.finish_reason=='length' else graph_payload(content)
        raw_rows.extend(payload.get('assertions', []))
        accepted, failed = validate_assertions(payload, fetched, document=document)
        rejected += failed
        for row in accepted:
            key = assertion_key(row)
            if key not in seen:
                seen.add(key); rows.append(row)
        if not payload.get('has_more'):
            complete = True; break
        messages += [{'role': 'assistant', 'content': json.dumps(payload, ensure_ascii=False)},
                     {'role': 'user', 'content': '只用已读正文继续剩余内容，不重复先前结果，完整结束 JSON。'}]
    client.close()
    write(folder/output_name, {'assertions': raw_rows, 'validated_assertions': rows,
        'rejected': rejected, 'declared_complete': complete})


def cognee(endpoint, fixture_path, folder, python, *, smoke=False):
    # Do not inherit .env-loaded provider keys or unrelated service credentials.
    required = {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP',
                'USERPROFILE','APPDATA','LOCALAPPDATA','PROGRAMFILES','PROGRAMFILES(X86)'}
    env = {key: value for key, value in os.environ.items() if key.upper() in required}
    env.update(LLM_PROVIDER='openai', LLM_MODEL='openai/benchmark-model',
        LLM_ENDPOINT=endpoint+'/cognee/v1', LLM_API_KEY='benchmark-proxy',
        LLM_INSTRUCTOR_MODE='json_mode', LLM_MAX_COMPLETION_TOKENS='32768',
        LLM_TEMPERATURE='0', LLM_RATE_LIMIT_ENABLED='false',
        SYSTEM_ROOT_DIRECTORY=str(folder/'cognee-state'), DATA_ROOT_DIRECTORY=str(folder/'cognee-data'),
        DO_NOT_TRACK='1', TELEMETRY_DISABLED='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
        LITELLM_LOCAL_MODEL_COST_MAP='True', LOG_LEVEL='ERROR', PYTHONIOENCODING='utf-8')
    state = folder/'cognee-state'; state.mkdir(exist_ok=True)
    command = [str(python), str(Path(__file__).with_name('cognee_worker.py')),
        '--fixture', str(fixture_path), '--output', str(folder/('smoke.json' if smoke else 'cognee.json'))]
    if smoke: command.append('--smoke')
    with (folder/'cognee-worker.log').open('w', encoding='utf8') as log:
        result = subprocess.run(command, env=env, cwd=state, stdout=log, stderr=subprocess.STDOUT, timeout=260)
    if result.returncode:
        raise RuntimeError('Cognee worker failed; see cognee-worker.log')


def schema_comparison(config, endpoint, fixture, folder, *, suffix=''):
    """Explicit fallback: domain modeling experiment, not a Cognee SDK run."""
    from openai import OpenAI
    from scripts.graph_benchmark.typed_schema import DomainGraph, PROMPT
    schema = DomainGraph.model_json_schema()
    write(folder/'typed-schema.json', schema)
    messages = [{'role': 'system', 'content': PROMPT+'\n'+json.dumps(schema, ensure_ascii=False)},
                {'role': 'user', 'content': json.dumps({key: fixture[key] for key in ('document','focus','chunks')}, ensure_ascii=False)}]
    with OpenAI(api_key='benchmark-proxy', base_url=endpoint+'/cognee/v1', max_retries=0, timeout=190) as client:
        response = client.chat.completions.create(model=config['model'], messages=messages,
            max_tokens=32768, temperature=0, response_format={'type': 'json_object'})
    choice = response.choices[0]
    if choice.finish_reason=='length':
        raise ValueError('Typed-schema output was truncated; raw response preserved.')
    graph = DomainGraph.model_validate_json(choice.message.content or '')
    write(folder/('cognee-schema-result'+suffix+'.json'), graph.model_dump())
    write(folder/('cognee-schema-disclosure'+suffix+'.json'), {'mode': 'documented Cognee-style domain modeling',
        'native_cognee_sdk_executed': False, 'reason': 'SDK dependency downloads repeatedly timed out',
        'transport': 'existing OpenAI SDK; JSON mode; Pydantic validation',
        'ingestion_embeddings_storage_identity': 'not evaluated'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', default='output/cognee-comparison/fixture.json')
    parser.add_argument('--output', default='output/cognee-comparison')
    parser.add_argument('--python', default='output/cognee-poc-env/Scripts/python.exe')
    parser.add_argument('--backend', choices=['baseline', 'cognee', 'schema', 'both', 'pair', 'smoke'], default='both')
    parser.add_argument('--disable-thinking', action='store_true', help='Explicit controlled comparison pass; use the shared remaining call budget')
    args = parser.parse_args()
    folder, fixture_path, worker_python = [(ROOT/Path(value)).resolve() for value in (args.output, args.fixture, args.python)]
    if not folder.is_relative_to(ROOT/'output'):
        parser.error('Comparison artifacts must stay inside output/.')
    folder.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'fos_rag.settings')
    import django; django.setup()
    from kb.config import llm_settings
    config = llm_settings()
    if args.disable_thinking:
        config['_benchmark_disable_thinking'] = True
    if not config['remote_enabled'] and args.backend!='smoke':
        parser.error('The configured remote model is disabled.')
    fixture = json.loads(fixture_path.read_text(encoding='utf8'))
    proxy = BudgetProxy(config, folder)
    thread = threading.Thread(target=proxy.serve_forever, daemon=True); thread.start()
    endpoint = 'http://127.0.0.1:'+str(proxy.server_address[1])
    outcomes = {}
    suffix = '-no-thinking' if args.disable_thinking else ''
    backends = ['baseline', 'schema'] if args.backend=='pair' else ['baseline', 'cognee'] if args.backend=='both' else [args.backend]
    try:
        for backend in backends:
            started = time.monotonic()
            try:
                artifact = folder/(('cognee-schema-result' if backend=='schema' else backend)+suffix+'.json')
                if backend!='smoke' and artifact.exists():
                    outcomes[backend] = {'status': 'completed', 'reused_artifact': True}
                else:
                    if backend=='baseline': baseline(config, endpoint, fixture, folder,
                        output_name=artifact.name, max_calls=max(0, 2-proxy.counts['baseline']))
                    elif backend=='schema': schema_comparison(config, endpoint, fixture, folder, suffix=suffix)
                    else: cognee(endpoint, fixture_path, folder, worker_python, smoke=backend=='smoke')
                    outcomes[backend] = {'status': 'completed'}
            except Exception as exc:
                # Never print provider exception text or configuration secrets.
                outcomes[backend] = {'status': 'failed', 'error': type(exc).__name__}
                print(f'{backend}: failed ({type(exc).__name__}); inspect isolated logs.', flush=True)
            outcomes[backend]['seconds'] = round(time.monotonic()-started, 2)
    finally:
        proxy.shutdown(); proxy.server_close()
    write(folder/('run-'+args.backend+suffix+'.json'), {'model': config['model'], 'focus': fixture['focus'],
        'thinking_requested': 'disabled' if args.disable_thinking else 'provider default',
        'input_characters': sum(len(c['text']) for c in fixture['chunks']),
        'budget': {'requests_per_backend': 2, 'max_output_tokens_per_call': 32768, 'seconds_total': 600},
        'outcomes': outcomes, 'requests': proxy.records, 'production_writes': False})
    print(json.dumps(outcomes, ensure_ascii=False), flush=True)
    if any(item['status']=='failed' for item in outcomes.values()): return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
