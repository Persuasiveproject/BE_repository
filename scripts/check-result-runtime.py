"""Check installer rendering and runtime isolation without host changes (Docker mocked)."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runtime = load('runtime_checks', 'infra/runtime/runtime.py')
valid = {'State': {'Running': True}, 'Config': {
    'Image': '149320913208.dkr.ecr.ap-southeast-2.amazonaws.com/yeongeopjung-dev-api:' + 'a'*40,
    'User': '1000:1000', 'Env': ['DJANGO_DEBUG=false']}, 'HostConfig': {'Privileged': False}}
runtime.docker = lambda *args: json.dumps([valid])
assert runtime.api_config('development')[0] == 'yeongeopjung-dev-api'
for label, alter in [
    ('wrong image', lambda c: c['Config'].update(Image='redis:7.4-alpine')),
    ('root user', lambda c: c['Config'].update(User='0')),
    ('privileged', lambda c: c['HostConfig'].update(Privileged=True)),
    ('stopped', lambda c: c['State'].update(Running=False)),
    ('newline environment', lambda c: c['Config'].update(Env=['A=value\nINJECTED=value'])),
]:
    candidate = copy.deepcopy(valid)
    alter(candidate)
    runtime.docker = lambda *args: json.dumps([candidate])
    try:
        runtime.api_config('development')
    except RuntimeError:
        pass
    else:
        raise AssertionError(label + ' was accepted')

installer = load('installer_checks', 'scripts/install-result-runtime.py')
with tempfile.TemporaryDirectory() as folder:
    runtime.BASE = Path(folder)
    (runtime.BASE / 'recover_results.py').write_text('# compatibility fixture\n')
    real_run = subprocess.run
    try:
        for probe_code in (0, 1, 125):
            calls = []
            def fake_run(args, **kwargs):
                calls.append(args)
                return SimpleNamespace(returncode=probe_code if len(calls) == 1 else 0)
            runtime.subprocess.run = fake_run
            if probe_code == 125:
                try:
                    runtime.run_results('test-api')
                except RuntimeError:
                    pass
                else:
                    raise AssertionError('Probe error was silently ignored')
                assert len(calls) == 1
            else:
                runtime.run_results('test-api')
                assert len(calls) == 2
                if probe_code == 0:
                    assert calls[-1][-3:] == ['python', 'manage.py', 'recover_results']
                else:
                    assert calls[-1][-2:] == ['python', '-']
    finally:
        runtime.subprocess.run = real_run

for environment in ('development', 'production'):
    run = subprocess.run([sys.executable, 'scripts/install-result-runtime.py', environment, '--render-only'],
                         cwd=ROOT, check=True, text=True, capture_output=True)
    units = json.loads(run.stdout)
    assert len(units) == 3
    assert 'OnUnitInactiveSec=10' in units[f'ye-results-{environment}.timer']
    assert f'runtime.py results {environment}' in units[f'ye-results-{environment}.service']
    assert f'runtime.py worker {environment}' in units[f'ye-worker-{environment}.service']
    assert all('celery beat' not in source for source in units.values())
bad = subprocess.run([sys.executable, 'scripts/install-result-runtime.py', 'invalid', '--render-only'],
                     cwd=ROOT, text=True, capture_output=True)
assert bad.returncode != 0
try:
    installer.render_units('invalid')
except ValueError:
    pass
else:
    raise AssertionError('Invalid runtime environment accepted')
print('Runtime isolation guards and two-environment unit rendering passed (Docker mocked; no host changes)')
