"""Read-only checks against the frozen executable, without user Python on PATH."""
import json
import os
import subprocess
from pathlib import Path


def smoke_runtime(executable: Path) -> dict:
    env = dict(os.environ)
    env.pop('PYTHONHOME', None)
    env.pop('PYTHONPATH', None)
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    env['PATH'] = (str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32')
                   if os.name == 'nt' else '/usr/bin:/bin:/usr/sbin:/sbin')
    result = subprocess.run([str(executable.resolve()), '--doctor'], capture_output=True,
                            encoding='utf-8', env=env, timeout=90, check=True)
    report = json.loads(result.stdout)
    if not report.get('ok'):
        raise RuntimeError(f'Runtime health check failed: {report}')
    messages = [dict(jsonrpc='2.0', id=1, method='initialize', params={}),
                dict(jsonrpc='2.0', id=2, method='tools/list'),
                dict(jsonrpc='2.0', id=3, method='resources/list')]
    result = subprocess.run([str(executable.resolve()), '--mcp'],
                            input=''.join(json.dumps(item)+'\n' for item in messages),
                            capture_output=True, encoding='utf-8', env=env, timeout=30, check=True)
    replies = {item['id']: item for item in map(json.loads, result.stdout.splitlines())}
    assert replies[1]['result']['serverInfo']['name'] == 'freeup-space'
    assert any(tool['name'] == 'open_widget' for tool in replies[2]['result']['tools'])
    uri = replies[3]['result']['resources'][0]['uri']
    result = subprocess.run([str(executable.resolve()), '--mcp'],
                            input=json.dumps(dict(jsonrpc='2.0', id=4, method='resources/read', params={'uri':uri}))+'\n',
                            capture_output=True, encoding='utf-8', env=env, timeout=30, check=True)
    assert 'Confirm move to Trash' in json.loads(result.stdout)['result']['contents'][0]['text']
    return {**report, 'mcp': True, 'system_python_required': False}

