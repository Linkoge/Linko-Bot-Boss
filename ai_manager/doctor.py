"""Read-only installation diagnostics. Never echo host configuration or subprocess output."""
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from . import isolation

TIMEOUT = 3
SERVICE = 'linko-bot-manager.service'


def command(argv, *, env=None):
    try:
        result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=TIMEOUT)
        return result.returncode, result.stdout.strip()
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return -1, ''


def environment_file(path):
    """Read systemd KEY=VALUE assignments without executing shell code or expanding variables.

    Support quoted values, embedded quotes, comments and line continuations. Reject
    unsupported multiline values explicitly rather than guessing the service's PATH.
    """
    values = {}
    for line in path.read_text().replace('\\\n', '').splitlines():
        line = line.strip()
        if not line or line.startswith(('#', ';')):
            continue
        key, sep, value = line.partition('=')
        if not sep or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', key.strip()):
            raise ValueError('Invalid assignment')
        # systemd preserves spaces in unquoted values, unlike shell tokenization.
        if value.strip().startswith(('"', "'")):
            parts = shlex.split(value, comments=False)
            if len(parts) != 1:
                raise ValueError('Unsupported assignment')
            value = parts[0]
        values[key.strip()] = value.strip()
    return values


def health(url, host='localhost'):
    # Ignore shell proxy settings; this is a direct probe, with no credentials/cookies.
    request = Request(url.rstrip('/') + '/api/auth-status', headers={'Host': host})
    with build_opener(ProxyHandler({})).open(request, timeout=TIMEOUT) as response:
        if response.status != 200:
            raise ValueError('Unhealthy HTTP status')
        return json.loads(response.read(65536))


class Doctor:
    def __init__(self, home=None, repo=None, output=print, isolation_config=isolation.CONFIG):
        self.home = home or Path.home()
        self.repo = repo or Path(__file__).resolve().parents[1]
        self.output = output
        self.failed = False
        self.isolation_config = isolation_config

    def report(self, ok, label, fix, *, warning=False):
        status = 'PASS' if ok else 'WARN' if warning else 'FAIL'
        self.failed |= status == 'FAIL'
        self.output(f'{status} {label}' + ('' if ok else f' — Fix: {fix}'))

    def properties(self, service):
        code, text = command(['systemctl', '--user', 'show', service, '--no-pager',
                              '--property=LoadState,UnitFileState,ActiveState,Environment,EnvironmentFiles,MainPID'])
        return dict(line.split('=', 1) for line in text.splitlines() if '=' in line) if code == 0 else {}

    def read_env(self, path, label):
        try:
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('Expected regular env file')
            self.report(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) & 0o077 == 0,
                        label + ' env permissions', 'chown this env file to the service user; chmod 600 it')
            env = environment_file(path)
        except (OSError, ValueError, UnicodeError):
            self.report(False, label + ' env file exists and is readable/valid',
                        'create the owner-only env file described in docs/INSTALL.md (plain KEY=VALUE)')
            return {}
        self.report(True, label + ' env file exists and is readable/valid', '')
        return env

    def instance(self, service, path, expected, *, url=None, remote=False):
        label = service
        env = self.read_env(path, label) if not remote else {}
        props = self.properties(service) if not remote else {}
        if remote:
            self.report(False, label + ' remote user unit/environment',
                        'run doctor while logged in as that instance service user', warning=True)
        else:
            for key, wanted, fix in (
                ('LoadState', 'loaded', 'install the service with scripts/install_service.py'),
                ('UnitFileState', 'enabled', f'systemctl --user enable {service}'),
                ('ActiveState', 'active', f'systemctl --user start {service}; inspect its journal if it fails'),
            ):
                self.report(props.get(key) == wanted, label + ' ' + key, fix)
            files = re.findall(r'(?:^|\s)(.+?) \(ignore_errors=(?:yes|no)\)', props.get('EnvironmentFiles', ''))
            self.report(str(path) in files, label + ' EnvironmentFile',
                        'point the unit EnvironmentFile at its matching ~/.config/linko-bot-manager*.env; daemon-reload and restart')
            try:
                base = dict(item.split('=', 1) for item in shlex.split(props.get('Environment', '')) if '=' in item)
            except ValueError:
                base = {}
            # Never use the invoking login shell's PATH as the service PATH.
            code, manager = command(['systemctl', '--user', 'show-environment'])
            inherited = dict(line.split('=', 1) for line in manager.splitlines() if '=' in line) if code == 0 else {}
            effective = {**inherited, **base, **env}
            pid = props.get('MainPID', '0')
            if pid.isdigit() and int(pid) > 0:
                try:
                    entries = Path(f'/proc/{pid}/environ').read_bytes().decode().split('\0')
                    running = dict(item.split('=', 1) for item in entries if '=' in item)
                    self.report(all(running.get(k) == v for k, v in env.items()),
                                label + ' running environment matches env file', f'systemctl --user restart {service}')
                    effective = running
                except (OSError, UnicodeError):
                    self.report(False, label + ' running environment readable',
                                'run doctor as the service user; restart the service if its process has exited')
            env = effective
            self.providers(env, label)
            self.report(bool(env.get('XAI_API_KEY', '').strip()), label + ' XAI_API_KEY set',
                        'set XAI_API_KEY in the owner-only env file and restart the service')
            for binary in ('ffmpeg', env.get('AI_MANAGER_AUDIO_PROBE', 'ffprobe')):
                self.report(bool(shutil.which(binary, path=env.get('PATH', os.defpath))),
                            label + ' audio executable present', 'install ffmpeg and include its directory in service PATH')
            hosts = [h.strip() for h in env.get('AI_MANAGER_ALLOWED_HOSTS', '').split(',') if h.strip()]
            self.report(bool(hosts), label + ' allowed hosts configured',
                        'set AI_MANAGER_ALLOWED_HOSTS including the browser hostname and localhost; restart the service')
        port = env.get('AI_MANAGER_PORT', '8787')
        try:
            if not url and (not port.isdigit() or not 0 < int(port) < 65536):
                raise ValueError('Invalid port')
            result = health(url or f'http://127.0.0.1:{port}')
            if not isinstance(result, dict):
                raise ValueError('Invalid health response')
            self.report(result.get('scheduler_running') is True, label + ' HTTP health/scheduler',
                        'inspect the service journal; confirm AI_MANAGER_PORT and allow localhost')
            self.report(bool(expected) and result.get('release_commit') == expected,
                        label + ' release matches checkout HEAD',
                        f'restart {service} after updating, or deploy every registered instance together')
        except (OSError, ValueError, TypeError):
            self.report(False, label + ' HTTP health/release',
                        'start the service; check port, allowed localhost, and journal; then rerun doctor')
        return env

    def providers(self, env, label):
        path = env.get('PATH', os.defpath)
        child_env = {**env, 'PATH': path, 'HOME': str(self.home)}
        for provider in ('claude', 'codex'):
            configured = env.get('AI_MANAGER_' + provider.upper(), provider)
            binary = shutil.which(configured, path=path)
            if provider == 'claude' and not binary and 'AI_MANAGER_CLAUDE' not in env:
                binary = shutil.which(str(self.home / '.local/bin/claude'), path=path)
            self.report(bool(binary), label + ' ' + provider + ' executable',
                        f'set AI_MANAGER_{provider.upper()} to its absolute executable path and restart')
            if provider == 'codex' and binary:
                # nvm's npm launcher uses /usr/bin/env node, which needs the service PATH.
                try:
                    with Path(binary).open('rb') as stream:
                        launcher = stream.read(256)
                except OSError:
                    launcher = b''
                needs_node = b'node' in launcher.split(b'\n', 1)[0]
                self.report(not needs_node or bool(shutil.which('node', path=path)),
                            label + ' Codex node runtime', 'add the nvm version bin directory to PATH in the env file and restart')
                code, _ = command([binary, '--version'], env=child_env)
                self.report(code == 0, label + ' Codex startup',
                            'install a working Codex runtime; include node/nvm bin in service PATH and restart')

    def run(self):
        _, expected = command(['git', '-C', str(self.repo), 'rev-parse', 'HEAD'])
        self.report(bool(re.fullmatch('[0-9a-f]{40,64}', expected)), 'checkout HEAD available',
                    'run doctor from a complete Git checkout with git installed')
        _, linger = command(['loginctl', 'show-user', str(os.getuid()), '--property=Linger', '--value'])
        self.report(linger == 'yes', 'user linger enabled', 'loginctl enable-linger "$USER"')
        env = self.instance(SERVICE, self.home / '.config/linko-bot-manager.env', expected)
        data = Path(env.get('AI_MANAGER_DATA', str(self.home / '.local/share/linko-bot-manager'))).expanduser()
        instances = {}
        try:
            installed = json.loads((data / 'deploy/installed.json').read_text())
            if not isinstance(installed, dict) or not isinstance(installed.get('instances', []), list):
                raise ValueError('Invalid registry')
            for item in installed.get('instances', []):
                if not isinstance(item, dict) or not isinstance(item.get('name'), str):
                    raise ValueError('Invalid instance')
                instances[item['name']] = item
        except FileNotFoundError:
            self.report(False, 'coordinated deploy installed',
                        'run python3 scripts/install_deploy.py to update all instances together', warning=True)
        except (OSError, ValueError, KeyError, TypeError):
            self.report(False, 'deploy instance registry readable', 'repair DATA/deploy/installed.json using install_deploy.py')
        for path in (self.home / '.config').glob('linko-bot-manager-*.env'):
            name = path.name[len('linko-bot-manager-'):-4]
            instances.setdefault(name, {'name': name})
        _, units = command(['systemctl', '--user', 'list-unit-files', 'linko-bot-manager@*.service', '--no-legend', '--no-pager'])
        for name in re.findall(r'linko-bot-manager@([a-z0-9-]+)\.service', units):
            instances.setdefault(name, {'name': name})
        for name, item in instances.items():
            if not re.fullmatch('[a-z0-9-]+', name):
                self.report(False, 'instance name valid', 'repair deploy registry instance names')
                continue
            self.instance(f'linko-bot-manager@{name}.service',
                          self.home / f'.config/linko-bot-manager-{name}.env', expected,
                          url=item.get('url'), remote=bool(item.get('service_cmds')))
        repair = data / 'repair'
        self.report(all((repair / p).is_file() for p in ('installed.json', 'launcher.py')),
                    'repair agent installed', 'run python3 scripts/install_repair.py install --conversation CHAT_ID')
        props = self.properties('linko-bot-manager-repair.timer')
        self.report(props.get('ActiveState') == 'active' and props.get('UnitFileState') == 'enabled',
                    'repair timer enabled and active',
                    'run python3 scripts/install_repair.py install; check systemctl --user status linko-bot-manager-repair.timer')
        self.worker_isolation(data)
        return int(self.failed)

    def worker_isolation(self, data):
        """Worker user present and actually isolated; only the repair launcher reaches root; no blanket NOPASSWD."""
        found = isolation.detect(self.isolation_config)
        install = 'sudo python3 scripts/install_worker_user.py install (README "How work runs")'
        self.report(bool(found), 'worker user installed', install + "; until then workers run with your user's rights",
                    warning=True)
        _, listing = command(['sudo', '-n', '-l'])
        self.report(not isolation.blanket_nopasswd(listing), 'no blanket NOPASSWD sudo for the service user',
                    'remove the temporary NOPASSWD: ALL rule (README "Emergency host repair")', warning=not found)
        if not found:
            return
        config = json.loads(Path(self.isolation_config).read_text())

        def same(installed, source):
            try:
                return hashlib.sha256(Path(installed).read_bytes()).digest() == \
                    hashlib.sha256((self.repo / source).read_bytes()).digest()
            except OSError:
                return False
        self.report(same(found.launcher, 'scripts/worker_launcher.py') and
                    same(config.get('repair_launcher', ''), 'scripts/emergency_repair.py'),
                    'root-owned launchers match this checkout', install)
        code, text = command([*found.prefix(), 'probe', '--read', str(self.home / '.config/linko-bot-manager.env'),
                              '--read', str(self.home / '.ssh'), '--read', str(data / 'manager.sqlite3'),
                              '--write', str(data / 'repair')])
        try:
            result = json.loads(text) if code == 0 else {}
            reached = [*result['read'].values(), *result['write'].values()]
        except (ValueError, KeyError, TypeError, AttributeError):
            reached = [True]
        self.report(not any(reached), 'worker isolation working (no env file, ~/.ssh, database or repair spool)',
                    install + '; then rerun doctor')
        unit = self.home / '.config/systemd/user/linko-bot-manager-repair.service'
        try:
            privileged = str(config.get('repair_launcher')) in unit.read_text()
        except OSError:
            privileged = False
        self.report(privileged, 'repair timer runs the root-owned launcher',
                    'as the service user: python3 scripts/install_repair.py install', warning=True)


def main():
    return Doctor().run()
