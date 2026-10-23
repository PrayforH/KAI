"""Run on the target host after API health is verified. Never print credentials."""
import json
import os
from pathlib import Path
import secrets
import urllib.request

root = Path('/home/xiaokai/deployments/kai-assistant-20261023')
config = json.loads((root / 'private/compose.json').read_text())
headers = {'Content-Type': 'application/json', 'X-Harness-Service-Token': config['services']['api']['environment']['HARNESS_API_BEARER_TOKEN']}
login = root / 'private/login.json'
if not login.exists():
    account = {'email': 'xiaokai@kai.local', 'password': secrets.token_urlsafe(24), 'display_name': '小凯'}
    request = urllib.request.Request('http://127.0.0.1:18800/v1/auth/register', data=json.dumps(account).encode(), headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        assert response.status == 201
    fd = os.open(login, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(account, stream)
    print('Created deployment owner; credentials saved in private/login.json')
else:
    print('Existing deployment owner credentials retained')
