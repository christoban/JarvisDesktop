#!/usr/bin/env python
import requests
import json
import time

BASE_URL = 'http://localhost:7071'
TOKEN = 'menedona_2005_christoban_2026'

commands = [
    'ouvre chrome',
    'non ouvre un nouvel onglet plutot',
    'referme la fenetre que tu viens d\'ouvrir',
    'ferme le nouvel onglet'
]

for i, cmd in enumerate(commands, 1):
    print(f'\n🎤 [{i}] Sending: {cmd!r}')
    try:
        r = requests.post(
            f'{BASE_URL}/api/command',
            json={'command': cmd},
            headers={
                'X-Jarvis-Token': TOKEN,
                'X-Device-Id': 'NDZANA_PHONE'
            },
            timeout=30
        )
        print(f'Response: {r.status_code}')
        if r.status_code in [200, 202]:
            data = r.json()
            print(f'Result ID: {data.get("result_id")}')
            print(f'Status: {data.get("status")}')
            if 'message' in data:
                print(f'Message: {data.get("message")}')
    except Exception as e:
        print(f'Error: {e}')
    time.sleep(3)

print('\n✅ All commands sent. Check server logs.') 
