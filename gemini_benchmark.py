#!/usr/bin/env python3
"""Gemini timing/token tester for Raspberry Pi OS; Python standard library only.

Run: python3 gemini_benchmark.py --prompt "Explain Raspberry Pi in one sentence."
Repeated trials: add --repeat 5 --label baseline
Pick another model: --model MODEL_ID; discover available IDs with --list-models.
Set GEMINI_API_KEY in the environment or enter it at the hidden prompt.
Measurements append to gemini_metrics.csv; no prompts, answers or API keys are logged.
This measures a text API request, not a file-search agent or model-only compute time.
"""
import argparse
import csv
import datetime as dt
import getpass
import hashlib
import json
import os
import platform
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = 'https://generativelanguage.googleapis.com/v1beta'
USAGE_FIELDS = {
    'input_tokens': 'promptTokenCount',
    'output_tokens': 'candidatesTokenCount',
    'thinking_tokens': 'thoughtsTokenCount',
    'cached_input_tokens': 'cachedContentTokenCount',
    'tool_input_tokens': 'toolUsePromptTokenCount',
    'total_tokens': 'totalTokenCount',
}
FIELDS = ['timestamp_utc', 'label', 'trial', 'model', 'returned_model', 'machine',
          'prompt_sha256', 'elapsed_seconds', 'status', 'http_status', 'finish_reason',
          'input_tokens', 'output_tokens', 'thinking_tokens', 'cached_input_tokens',
          'tool_input_tokens', 'total_tokens', 'error']


def api_request(endpoint, key, payload=None, timeout=90):
    request = urllib.request.Request(
        BASE_URL + endpoint,
        data=json.dumps(payload).encode('utf-8') if payload is not None else None,
        headers={'x-goog-api-key': key, 'Content-Type': 'application/json'},
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        elapsed = time.perf_counter() - start
        try:
            message = json.loads(exc.read()).get('error', {}).get('message', str(exc))
        except (ValueError, AttributeError):
            message = 'Gemini returned an HTTP error'
        return None, elapsed, exc.code, str(message).replace(key, '[redacted]')[:500]
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, time.perf_counter() - start, '', str(exc).replace(key, '[redacted]')[:500]
    elapsed = time.perf_counter() - start
    try:
        return json.loads(body), elapsed, status, ''
    except ValueError:
        return None, elapsed, status, 'Response was not valid JSON'


def extract_result(response):
    """Missing usage stays unknown (blank), rather than a fabricated zero."""
    response = response or {}
    usage = response.get('usageMetadata') or {}
    metrics = {label: usage.get(field, '') for label, field in USAGE_FIELDS.items()}
    candidates = response.get('candidates') or []
    first = candidates[0] if candidates else {}
    parts = first.get('content', {}).get('parts', [])
    answer = '\n'.join(part['text'] for part in parts
                       if isinstance(part.get('text'), str) and not part.get('thought'))
    reason = first.get('finishReason', '')
    block = (response.get('promptFeedback') or {}).get('blockReason', '')
    return metrics, answer, reason or block


def append_row(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    if exists:
        with path.open(newline='', encoding='utf-8') as stream:
            if next(csv.reader(stream), []) != FIELDS:
                raise ValueError('Existing CSV has a different header; choose another --csv path')
    with path.open('a', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def positive_int(value):
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError('must be greater than zero')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--prompt', help='Prompt to send (otherwise asks interactively)')
    parser.add_argument('--model', default='gemini-2.5-flash', help='Model ID supported by your API key')
    parser.add_argument('--repeat', type=positive_int, default=1)
    parser.add_argument('--timeout', type=positive_int, default=90)
    parser.add_argument('--max-output-tokens', type=positive_int, default=2048)
    parser.add_argument('--label', default='baseline', help='Name for this experiment')
    parser.add_argument('--csv', type=Path, default=Path('gemini_metrics.csv'))
    parser.add_argument('--list-models', action='store_true')
    args = parser.parse_args()
    key = os.environ.get('GEMINI_API_KEY', '').strip() or getpass.getpass('Gemini API key (hidden): ').strip()
    if not key:
        parser.error('An API key is required')
    if args.list_models:
        page = ''
        while True:
            suffix = '/models' + ('?pageToken=' + urllib.parse.quote(page, safe='') if page else '')
            data, _, _, error = api_request(suffix, key, timeout=args.timeout)
            if error:
                print('Error:', error)
                return 1
            for model in data.get('models', []):
                if 'generateContent' in model.get('supportedGenerationMethods', []):
                    print(model['name'].removeprefix('models/'))
            page = data.get('nextPageToken', '')
            if not page:
                return 0
    prompt = args.prompt if args.prompt is not None else input('Prompt: ')
    if not prompt.strip():
        parser.error('Prompt cannot be empty')
    model = args.model.removeprefix('models/')
    if not model or '/' in model:
        parser.error('Supply a model ID, not a URL')
    payload = {'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
               'generationConfig': {'temperature': 0, 'maxOutputTokens': args.max_output_tokens}}
    successes = []
    failures = 0
    for trial in range(1, args.repeat + 1):
        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        data, elapsed, http_status, error = api_request(
            '/models/' + urllib.parse.quote(model, safe='') + ':generateContent', key, payload, args.timeout)
        metrics, answer, finish = extract_result(data)
        status = 'error' if error else ('ok' if answer else 'no_text')
        row = {'timestamp_utc': timestamp, 'label': args.label, 'trial': trial, 'model': model,
               'returned_model': (data or {}).get('modelVersion', ''), 'machine': platform.machine(),
               'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
               'elapsed_seconds': round(elapsed, 6), 'status': status, 'http_status': http_status,
               'finish_reason': finish, 'error': error, **metrics}
        append_row(args.csv, row)
        print(f'\nTrial {trial}/{args.repeat} | {elapsed:.3f} seconds | {status} | finish: {finish or "unknown"}')
        if answer:
            print('\n' + answer)
            successes.append(elapsed)
        else:
            failures += 1
            print(error or 'No answer text returned. Check finish reason; MAX_TOKENS may require a higher output limit.')
        print('\nTokens reported by Gemini:')
        for label, count in metrics.items():
            print(f'  {label}: {count if count != "" else "not reported"}')
        if error:
            print('Check internet/key permissions. For model errors, try --list-models and choose --model MODEL_ID.')
    if successes:
        print(f'\nSuccessful text responses: {len(successes)}/{args.repeat}; median time: {statistics.median(successes):.3f}s')
    print('CSV saved to:', args.csv.resolve())
    print('Timing includes network, server processing and response transfer; automatic retries are disabled.')
    print('Cached input is part of input usage; thinking is separate from visible output. Use reported total, not a sum of columns.')
    return 1 if failures else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('\nStopped.')
        raise SystemExit(130)
    except (OSError, ValueError) as exc:
        print('Local error:', exc)
        raise SystemExit(1)
