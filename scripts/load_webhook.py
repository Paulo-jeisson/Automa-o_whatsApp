#!/usr/bin/env python3
"""Carga controlada do webhook. Use somente em ambiente autorizado."""
import argparse
import hashlib
import hmac
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen


def send(index, args):
    payload = json.dumps({
        'object': 'whatsapp_business_account',
        'entry': [{'changes': [{'value': {
            'metadata': {'phone_number_id': args.phone_number_id},
            'contacts': [{'wa_id': f'5511{index:09d}', 'profile': {'name': f'Load {index}'}}],
            'messages': [{
                'id': f'load-{args.run_id}-{index}', 'from': f'5511{index:09d}',
                'timestamp': str(int(time.time())), 'type': 'text',
                'text': {'body': 'Teste de carga autorizado'},
            }],
        }, 'field': 'messages'}]}],
    }).encode()
    signature = 'sha256=' + hmac.new(args.app_secret.encode(), payload, hashlib.sha256).hexdigest()
    request = Request(
        args.url, data=payload, method='POST',
        headers={'Content-Type': 'application/json', 'X-Hub-Signature-256': signature},
    )
    started = time.monotonic()
    try:
        with urlopen(request, timeout=args.timeout) as response:
            return response.status, (time.monotonic() - started) * 1000
    except Exception:
        return 0, (time.monotonic() - started) * 1000


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--app-secret', required=True)
    parser.add_argument('--phone-number-id', required=True)
    parser.add_argument('--requests', type=int, default=100)
    parser.add_argument('--concurrency', type=int, default=10)
    parser.add_argument('--timeout', type=float, default=10)
    parser.add_argument('--run-id', default=str(int(time.time())))
    args = parser.parse_args()
    if args.requests < 1 or args.requests > 100_000 or args.concurrency < 1 or args.concurrency > 500:
        parser.error('Limites: requests 1..100000 e concurrency 1..500')
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(lambda index: send(index, args), range(args.requests)))
    latencies = [latency for _, latency in results]
    success = sum(1 for status, _ in results if 200 <= status < 300)
    ordered = sorted(latencies)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * .95))]
    print(json.dumps({
        'requests': len(results), 'success': success, 'failed': len(results) - success,
        'mean_ms': round(statistics.mean(latencies), 2), 'p95_ms': round(p95, 2),
    }))
    raise SystemExit(0 if success == len(results) else 1)


if __name__ == '__main__':
    main()
