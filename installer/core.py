"""Per-user, versioned setup using only the supported Codex plugin CLI.

No scans of user data are performed here. Runtime diagnostics create their own
synthetic temporary fixtures. Old payload versions are deliberately retained.
"""
from __future__ import annotations

import contextlib
import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

PLUGIN_ID = 'freeup-space@freeup-space'
MARKER = '.freeup-space-installer.json'
OWNER = 'freeup-space-self-contained-setup-v1'
RESOURCE_URI = 'ui://freeup-space/widget-v1.html'


class SetupError(RuntimeError):
    """An installation problem that can be presented to the user."""


@contextlib.contextmanager
def _external_environment():
    """Undo PyInstaller's library injection for external executables."""
    env = os.environ.copy()
    bundle = getattr(sys, '_MEIPASS', None)
    if bundle:
        env['PATH'] = os.pathsep.join(p for p in env.get('PATH', '').split(os.pathsep)
                                     if p and not Path(p).resolve().is_relative_to(Path(bundle).resolve()))
        for key in ('LD_LIBRARY_PATH', 'LIBPATH'):
            if key + '_ORIG' in env:
                env[key] = env[key + '_ORIG']
            else:
                env.pop(key, None)
    reset_dll = sys.platform == 'win32' and bool(getattr(sys, 'frozen', False))
    if reset_dll:
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    try:
        yield env
    finally:
        if reset_dll:
            ctypes.windll.kernel32.SetDllDirectoryW(str(bundle) if bundle else None)


def _run(args, *, input=None, timeout=90):
    try:
        with _external_environment() as env:
            result = subprocess.run([str(arg) for arg in args], input=input,
                                    capture_output=True, encoding='utf-8', errors='replace',
                                    timeout=timeout, check=True, env=env,
                                    **({'creationflags': 0x08000000} if sys.platform == 'win32' else {}))
        return result.stdout
    except (OSError, subprocess.SubprocessError) as error:
        detail = getattr(error, 'stderr', None) or str(error)
        raise SetupError(f"Could not run {Path(args[0]).name} {' '.join(str(a) for a in args[1:])}: {detail}") from error


def _json_command(args):
    try:
        value = json.loads(_run(args))
        if not isinstance(value, dict):
            raise ValueError('expected an object')
        return value
    except (ValueError, TypeError) as error:
        raise SetupError('Codex or runtime returned an invalid JSON response. Update Codex and retry.') from error


def _mac_application_roots():
    return [Path('/Applications'), Path.home() / 'Applications']


def discover_codex() -> list[Path]:
    candidates = []
    found = shutil.which('codex')
    if found:
        candidates.append(Path(found))
    if sys.platform == 'darwin':
        for root in _mac_application_roots():
            for name in ('Codex.app', 'ChatGPT.app'):
                for relative in ('Contents/Resources/codex', 'Contents/Resources/bin/codex'):
                    candidates.append(root / name / relative)
    elif sys.platform == 'win32':
        local = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local')))
        roots = [local / 'Programs' / name for name in ('Codex', 'ChatGPT')]
        powershell = shutil.which('powershell.exe')
        if powershell:
            # Fixed command, restricted to OpenAI packages; no user-supplied shell text.
            script = "@(Get-AppxPackage | Where-Object { $_.Name -match '^OpenAI[.]' } | Select-Object -ExpandProperty InstallLocation) | ConvertTo-Json -Compress"
            try:
                packages = json.loads(_run([powershell, '-NoProfile', '-NonInteractive', '-Command', script], timeout=15) or '[]')
                roots.extend(Path(p) for p in ([packages] if isinstance(packages, str) else packages) if p)
            except (SetupError, ValueError, TypeError):
                pass  # The GUI also allows browsing for a CLI.
        for root in roots:
            for relative in ('codex.exe', 'resources/codex.exe', 'app/resources/codex.exe',
                             'resources/bin/codex.exe', 'app/resources/bin/codex.exe'):
                candidates.append(root / relative)
    result = []
    for candidate in candidates:
        if candidate.is_file() and candidate.resolve() not in result:
            result.append(candidate.resolve())
    return result


def default_install_root() -> Path:
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'Freeup Space'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/Freeup Space'
    return Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'freeup-space'


def _read_json(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('expected an object')
        return value
    except (OSError, ValueError) as error:
        raise SetupError(f'Cannot read setup metadata at {path}: {error}') from error


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_bytes(data)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _encode(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def _entries(value, key):
    entries = value.get(key)
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise SetupError(f'Codex returned an unsupported {key} schema. Update Codex and retry.')
    return entries


def _health(runtime, version):
    doctor = _json_command([runtime, '--doctor'])
    if doctor.get('ok') is not True or doctor.get('version') != version:
        raise SetupError('The bundled runtime did not pass diagnostics or its version does not match. Download setup again.')
    requests = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
         'params': {'protocolVersion': '2025-06-18', 'capabilities': {},
                    'clientInfo': {'name': 'freeup-space-setup', 'version': '1'}}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'resources/read', 'params': {'uri': RESOURCE_URI}},
    ]
    output = _run([runtime, '--mcp'], input=''.join(json.dumps(r) + '\n' for r in requests), timeout=30)
    try:
        responses = {r['id']: r for r in (json.loads(line) for line in output.splitlines()) if 'id' in r}
        hello = responses[1]['result']
        contents = responses[2]['result']['contents']
        assert hello['serverInfo']['name'] == 'freeup-space'
        assert hello['protocolVersion'] == '2025-06-18'
        assert any(c.get('uri') == RESOURCE_URI and 'text/html' in c.get('mimeType', '') and c.get('text') for c in contents)
    except (ValueError, KeyError, TypeError, AssertionError) as error:
        raise SetupError('Runtime MCP handshake or widget resource verification failed. Download setup again.') from error


def _owned_repository(source):
    if not isinstance(source, dict) or source.get('sourceType') != 'git':
        return False
    remote = source.get('source', '')
    if not isinstance(remote, str):
        return False
    if remote.startswith('git@github.com:'):
        remote = 'https://github.com/' + remote[len('git@github.com:'):]
    parsed = urlparse(remote)
    path = parsed.path.rstrip('/').removesuffix('.git').lower()
    return (parsed.scheme in ('https', 'ssh') and parsed.hostname == 'github.com'
            and not parsed.query and not parsed.fragment and path == '/raviasha/freeup_space')


@contextlib.contextmanager
def _destination_lock(destination):
    user_key = hashlib.sha256(str(Path.home()).encode('utf-8')).hexdigest()[:16]
    destination_key = hashlib.sha256(os.path.normcase(str(destination.resolve())).encode('utf-8')).hexdigest()
    directory = Path(tempfile.gettempdir()) / ('freeup-space-setup-' + user_key)
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        handle = (directory / (destination_key + '.lock')).open('a+b')
    except OSError as error:
        raise SetupError(f'Cannot prepare the setup lock: {error}') from error
    # Keep the lock file: unlinking it allows waiters to lock different inodes.
    # Closing this handle (including on process death) releases the OS lock.
    with handle:
        try:
            if os.name == 'nt':
                import msvcrt
                if handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b'\0')
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise SetupError('Cannot continue because another setup is running for this folder. Wait for it to finish and retry.') from error
            raise SetupError(f'Cannot acquire the setup lock: {error}') from error
        yield


def install(payload: Path, destination: Path, codex: Path, progress=lambda message: None) -> dict:
    """Serialize all state reads, installation and rollback for one destination."""
    with _destination_lock(Path(destination)):
        return _install_unlocked(payload, destination, codex, progress)


def _install_unlocked(payload: Path, destination: Path, codex: Path, progress) -> dict:
    """Install/repair from an expanded setup payload; raise actionable SetupError."""
    payload, destination, codex = Path(payload).resolve(), Path(destination).resolve(), Path(codex).resolve()
    if not codex.is_file():
        raise SetupError('Codex CLI was not found. Install/update the Codex desktop app or browse to its bundled CLI.')
    progress('Checking Codex plugin support…')
    _run([codex, '--version'])
    for command in (['plugin', 'add'], ['plugin', 'remove'],
                    ['plugin', 'marketplace', 'add'], ['plugin', 'marketplace', 'remove']):
        help_text = _run([codex, *command, '--help'])
        if '--json' not in help_text:
            raise SetupError('This Codex CLI does not support the required plugin commands. Update the Codex desktop app.')
    marketplaces = _entries(_json_command([codex, 'plugin', 'marketplace', 'list', '--json']), 'marketplaces')
    previous_plugins = _entries(_json_command([codex, 'plugin', 'list', '--json']), 'installed')
    existing_market = next((m for m in marketplaces if m.get('name') == 'freeup-space'), None)
    if existing_market and (not existing_market.get('root') or Path(existing_market['root']).resolve() != destination):
        raise SetupError('A different freeup-space marketplace already exists. Choose its setup folder or resolve the marketplace name conflict in Codex.')
    catalog = destination / '.agents/plugins/marketplace.json'
    marker = destination / MARKER
    if catalog.exists() or marker.exists() or existing_market:
        if not marker.is_file() or _read_json(marker).get('owner') != OWNER:
            raise SetupError(f'The destination is not owned by Freeup Space setup: {destination}. Choose an empty folder.')
        if catalog.exists():
            old = _read_json(catalog)
            if old.get('name') != 'freeup-space' or any(p.get('name') != 'freeup-space' for p in old.get('plugins', [])):
                raise SetupError('The setup marketplace contains unrelated entries and will not be overwritten.')
    elif destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise SetupError(f'The destination contains files not owned by Freeup Space setup: {destination}. Choose an empty folder.')
    manifest = _read_json(payload / 'plugin/.codex-plugin/plugin.json')
    version = manifest.get('version')
    if manifest.get('name') != 'freeup-space' or not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+_-]{0,120}', version):
        raise SetupError('The setup payload has invalid plugin metadata. Download setup again.')
    executable = 'freeup-space.exe' if sys.platform == 'win32' else 'freeup-space'
    if not (payload / 'runtime' / executable).is_file():
        raise SetupError('The setup payload is missing its bundled runtime. Download the setup for this operating system again.')
    previous_catalog = catalog.read_bytes() if catalog.exists() else None
    previous_dedicated = next((p for p in previous_plugins if p.get('pluginId') == PLUGIN_ID and p.get('installed')), None)
    changed_catalog = False
    attempted_registration = False
    attempted_install = False
    try:
        progress('Copying the self-contained runtime…')
        # Retained versions remain installer-owned even if diagnostics or host
        # registration fail, so a subsequent Repair can safely use this folder.
        if not marker.exists():
            _atomic_write(marker, _encode({'owner': OWNER}))
        unique = uuid.uuid4().hex
        version_id = version + '-' + unique
        # Codex caches plugin files by manifest version. Each Repair must copy
        # the newly generated absolute runtime paths into a fresh cache entry.
        installed_version = version + ('.' if '+' in version else '+') + 'setup.' + unique
        version_root = destination / 'versions' / version_id
        version_root.mkdir(parents=True, exist_ok=False)
        shutil.copytree(payload / 'plugin', version_root / 'plugin', symlinks=True)
        shutil.copytree(payload / 'runtime', version_root / 'runtime', symlinks=True)
        plugin_root, runtime = version_root / 'plugin', version_root / 'runtime' / executable
        installed_manifest = dict(manifest, version=installed_version)
        _atomic_write(plugin_root / '.codex-plugin/plugin.json', _encode(installed_manifest))
        config = {'mcpServers': {'freeup-space': {'command': str(runtime), 'args': ['--mcp'],
                   'cwd': '.', 'startup_timeout_sec': 20, 'tool_timeout_sec': 120}}}
        _atomic_write(plugin_root / '.mcp.json', _encode(config))
        _atomic_write(plugin_root / '.runtime-path', (str(runtime) + '\n').encode('utf-8'))
        progress('Verifying runtime and widget…')
        _health(runtime, version)
        marketplace = {'name': 'freeup-space', 'interface': {'displayName': 'Freeup Space'},
                       'plugins': [{'name': 'freeup-space', 'source': {'source': 'local', 'path': f'./versions/{version_id}/plugin'},
                                    'policy': {'installation': 'AVAILABLE', 'authentication': 'ON_INSTALL'}, 'category': 'Utilities'}]}
        changed_catalog = True
        _atomic_write(catalog, _encode(marketplace))
        progress('Registering Freeup Space with Codex…')
        attempted_registration = True
        _json_command([codex, 'plugin', 'marketplace', 'add', destination, '--json'])
        attempted_install = True
        _json_command([codex, 'plugin', 'add', PLUGIN_ID, '--json'])
        installed = _entries(_json_command([codex, 'plugin', 'list', '--json']), 'installed')
        entry = next((p for p in installed if p.get('pluginId') == PLUGIN_ID), None)
        if not entry or entry.get('installed') is not True or entry.get('enabled') is not True:
            raise SetupError('Codex did not report Freeup Space as installed and enabled. Open Codex plugin settings and retry setup.')
        if entry.get('version') is not None and entry['version'] != installed_version:
            raise SetupError('Codex still reports a different Freeup Space version. Restart Codex and run Repair again.')
        source_path = entry.get('source', {}).get('path')
        if not source_path or Path(source_path).resolve() != plugin_root:
            raise SetupError('Codex reports a different Freeup Space source folder. Run Repair again after restarting Codex.')
        reported_config = _read_json(Path(source_path) / '.mcp.json')
        if reported_config != config:
            raise SetupError('The registered plugin runtime configuration does not match this installation.')
        _health(Path(reported_config['mcpServers']['freeup-space']['command']), version)
    except Exception as error:
        rollback_errors = []
        if changed_catalog:
            try:
                _atomic_write(catalog, previous_catalog) if previous_catalog is not None else catalog.unlink(missing_ok=True)
            except OSError as rollback_error:
                rollback_errors.append(str(rollback_error))
            # Restore host state after restoring the source catalog. Never touch personal catalogs.
            rollback_commands = []
            if attempted_install and not previous_dedicated:
                rollback_commands.append([codex, 'plugin', 'remove', PLUGIN_ID, '--json'])
            if attempted_registration and existing_market and previous_catalog is not None:
                rollback_commands.append([codex, 'plugin', 'marketplace', 'add', destination, '--json'])
                if attempted_install and previous_dedicated:
                    rollback_commands.append([codex, 'plugin', 'add', PLUGIN_ID, '--json'])
            elif attempted_registration and not existing_market:
                rollback_commands.append([codex, 'plugin', 'marketplace', 'remove', 'freeup-space', '--json'])
            for command in rollback_commands:
                try:
                    _json_command(command)
                except SetupError as rollback_error:
                    rollback_errors.append(str(rollback_error))
        suffix = (' Rollback needs attention: ' + '; '.join(rollback_errors)) if rollback_errors else ''
        raise SetupError(str(error) + suffix) from error
    warnings = []
    for entry in previous_plugins:
        old_id = entry.get('pluginId')
        if old_id != PLUGIN_ID and isinstance(old_id, str) and entry.get('name') == 'freeup-space' and entry.get('installed') is True and _owned_repository(entry.get('marketplaceSource')):
            try:
                progress(f'Removing the previous Freeup Space registration ({old_id})…')
                _json_command([codex, 'plugin', 'remove', old_id, '--json'])
            except SetupError as error:
                warning = f'The older registration {old_id} could not be removed and may remain active: {error}'
                warnings.append(warning)
                progress(warning)
    progress('Ready. Open a new Codex task and ask to open Freeup Space.')
    return {'version': version, 'installed_version': installed_version, 'warnings': warnings,
            'plugin_root': plugin_root, 'marketplace_path': catalog}
