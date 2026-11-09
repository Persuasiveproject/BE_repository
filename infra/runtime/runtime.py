"""Host-side launcher; current API image/environment is authoritative."""
import json
import os
from pathlib import Path
import subprocess
import sys

BASE = Path('/opt/ye-runtime')


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def api_config(environment):
    if environment not in ('development', 'production'):
        raise RuntimeError('Unexpected runtime environment')
    name = 'yeongeopjung-dev-api' if environment == 'development' else 'yeongeopjung-production-api'
    config = json.loads(docker('inspect', name))[0]
    if not config['State']['Running']:
        raise RuntimeError('API container is not running')
    image = config['Config']['Image']
    prefix = '149320913208.dkr.ecr.ap-southeast-2.amazonaws.com/' + name + ':'
    if not image.startswith(prefix):
        raise RuntimeError('Unexpected API image registry/repository')
    if config['Config'].get('User') not in ('1000', '1000:1000') or config['HostConfig'].get('Privileged'):
        raise RuntimeError('API runtime must use the unprivileged application user')
    if any('\n' in value or '\r' in value for value in config['Config']['Env']):
        raise RuntimeError('Unexpected newline in API environment')
    return name, config


def run_results(name):
    probe = subprocess.run(['docker', 'exec', name, 'python', '-c',
        'from pathlib import Path; raise SystemExit(0 if Path("/app/debates/management/commands/recover_results.py").is_file() else 1)'])
    if probe.returncode == 0:
        subprocess.run(['docker', 'exec', name, 'python', 'manage.py', 'recover_results'], check=True)
    elif probe.returncode == 1:
        # Compatibility for the pre-integration image. New images run their own
        # reviewed command, so subsequent source updates follow normal API CD.
        with (BASE / 'recover_results.py').open('rb') as stream:
            subprocess.run(['docker', 'exec', '-i', name, 'python', '-'], stdin=stream, check=True)
    else:
        raise RuntimeError('Could not inspect the API recovery command')


def run_closing(name):
    # Require the deployed image's reviewed command; do not inject closing code
    # into an old image whose models or migrations may differ.
    subprocess.run(['docker', 'exec', name, 'python', 'manage.py', 'close_debates'], check=True)


def main():
    mode, environment = sys.argv[1:]
    if environment not in ('development', 'production') or mode not in ('worker', 'results', 'closing'):
        raise SystemExit('Unexpected runtime mode/environment')
    name, config = api_config(environment)
    if mode == 'closing':
        run_closing(name)
        return
    worker_name = 'yeongeopjung-' + ('dev' if environment == 'development' else 'production') + '-worker'
    if mode == 'results':
        existing = subprocess.run(['docker', 'inspect', worker_name], text=True, capture_output=True)
        if existing.returncode == 0:
            worker = json.loads(existing.stdout)[0]
            if worker['Image'] != config['Image']:
                subprocess.run(['systemctl', 'try-restart', 'ye-worker-' + environment + '.service'], check=True)
        run_results(name)
        return
    runtime_dir = Path('/run/ye-runtime-' + environment)
    runtime_dir.mkdir(mode=0o700, exist_ok=True)
    envfile = runtime_dir / 'worker.env'
    contents = '\n'.join(config['Config']['Env']) + '\n'
    fd = os.open(envfile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(contents)
    envfile.chmod(0o600)
    existing = subprocess.run(['docker', 'inspect', worker_name], text=True, capture_output=True)
    if existing.returncode == 0:
        old = json.loads(existing.stdout)[0]
        if old['Config'].get('Labels', {}).get('ye.runtime') != 'worker':
            raise RuntimeError('Refusing to replace an unowned container')
        if old['State']['Running']:
            raise RuntimeError('Worker container already running outside this service')
        docker('rm', worker_name)
    mounts = []
    for mount in config['Mounts']:
        if mount['Type'] != 'bind' or mount['RW']:
            raise RuntimeError('Unexpected API runtime mount')
        mounts += ['--mount', f'type=bind,src={mount["Source"]},dst={mount["Destination"]},readonly']
    args = ['docker', 'run', '--rm', '--name', worker_name, '--label', 'ye.runtime=worker',
            '--label', 'ye.environment=' + environment, '--user', config['Config'].get('User') or '1000:1000',
            '--memory', '192m', '--cpus', '0.5', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--env-file', str(envfile), *mounts,
            config['Config']['Image'], 'celery', '-A', 'config', 'worker',
            '--pool=solo', '--concurrency=1', '--without-gossip', '--without-mingle',
            '--without-heartbeat', '--loglevel=WARNING', '--hostname=' + worker_name + '@%h']
    os.execvp(args[0], args)


if __name__ == '__main__':
    main()
