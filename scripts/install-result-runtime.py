"""Install the reviewed worker and CLOSED-result timer on an existing API host.

Run as root on the selected EC2, after its API/DB/Redis configuration is ready.
Does not provision Redis, change credentials, migrate databases, or start beat.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def render_units(environment):
    if environment not in ('development', 'production'):
        raise ValueError('Use development or production')
    worker_name = 'yeongeopjung-' + ('dev' if environment == 'development' else 'production') + '-worker'
    return {
        f'ye-worker-{environment}.service': f'''[Unit]
Description=Yeongup {environment} Celery worker
After=docker.service
Requires=docker.service

[Service]
Type=simple
RuntimeDirectory=ye-runtime-{environment}
RuntimeDirectoryMode=0700
ExecStart=/usr/bin/python3 /opt/ye-runtime/runtime.py worker {environment}
ExecStop=-/usr/bin/docker stop -t 45 {worker_name}
Restart=always
RestartSec=10
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
''',
        f'ye-results-{environment}.service': f'''[Unit]
Description=Yeongup {environment} CLOSED result recovery only
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/ye-runtime/runtime.py results {environment}
TimeoutStartSec=120
Nice=10
''',
        f'ye-results-{environment}.timer': f'''[Unit]
Description=Yeongup {environment} result recovery every 10 seconds

[Timer]
OnBootSec=30
OnUnitInactiveSec=10
AccuracySec=1
Unit=ye-results-{environment}.service

[Install]
WantedBy=timers.target
''',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('environment', choices=('development', 'production'))
    parser.add_argument('--render-only', action='store_true', help='Print units without inspecting or modifying the host')
    args = parser.parse_args()
    units = render_units(args.environment)
    if args.render_only:
        print(json.dumps(units, indent=2))
        return
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        raise SystemExit('Run this installer as root on the selected API EC2')
    runtime_source = ROOT / 'infra/runtime/runtime.py'
    command_source = ROOT / 'debates/management/commands/recover_results.py'
    if not runtime_source.is_file() or not command_source.is_file():
        raise SystemExit('Run from a complete reviewed repository checkout')
    if args.environment == 'production':
        marker = Path('/etc/yeongeopjung/environment')
        if not marker.exists() or marker.read_text().strip() != 'production':
            raise SystemExit('Production host marker is missing')
    spec = importlib.util.spec_from_file_location('ye_runtime', runtime_source)
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    runtime.api_config(args.environment)  # Verify the running API repository and container.
    target = Path('/opt/ye-runtime')
    target.mkdir(mode=0o755, parents=True, exist_ok=True)
    for source, name, mode in ((runtime_source, 'runtime.py', 0o700),
                               (command_source, 'recover_results.py', 0o644)):
        destination = target / name
        temporary = target / (name + '.new')
        shutil.copyfile(source, temporary)
        temporary.chmod(mode)
        temporary.replace(destination)
    for name, content in units.items():
        path = Path('/etc/systemd/system') / name
        path.write_text(content)
        path.chmod(0o644)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', f'ye-worker-{args.environment}.service'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', f'ye-results-{args.environment}.timer'], check=True)
    print('Worker and CLOSED result recovery enabled; beat and automatic closing are not enabled')


if __name__ == '__main__':
    main()
