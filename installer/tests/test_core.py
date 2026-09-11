import json
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from installer import core


class Host:
    def __init__(self, destination):
        self.destination = destination
        self.calls = []
        self.marketplaces = []
        self.installed = []
        self.fail = None
        self.disabled = False
        self.bad_resource = False
        self.fail_commands = {}

    def run(self, args, **kwargs):
        args = [str(a) for a in args]
        self.calls.append(args)
        assert kwargs['encoding'] == 'utf-8'
        assert kwargs['timeout'] > 0
        if self.fail and self.fail in args and ('--help' not in args or self.fail == '--help'):
            raise subprocess.CalledProcessError(1, args, stderr='synthetic failure')
        tail = args[1:]
        data = {}
        if tuple(tail) in self.fail_commands:
            raise subprocess.CalledProcessError(1, args, stderr=self.fail_commands[tuple(tail)])
        if tail[:2] in (['plugin', 'install'], ['plugin', 'uninstall']):
            raise subprocess.CalledProcessError(1, args, stderr='unknown plugin subcommand')
        if tail == ['--version']:
            return subprocess.CompletedProcess(args, 0, 'codex-cli 0.100.0', '')
        if '--help' in tail:
            return subprocess.CompletedProcess(args, 0, 'list add remove --json', '')
        if tail == ['plugin', 'marketplace', 'list', '--json']:
            data = {'marketplaces': self.marketplaces}
        elif tail == ['plugin', 'list', '--json']:
            data = {'installed': self.installed}
        elif tail[:3] == ['plugin', 'marketplace', 'add']:
            self.marketplaces = [{'name': 'freeup-space', 'root': str(self.destination)}]
        elif tail[:3] == ['plugin', 'marketplace', 'remove']:
            self.marketplaces = [m for m in self.marketplaces if m['name'] != tail[3]]
        elif tail[:2] == ['plugin', 'add']:
            catalog = json.loads((self.destination / '.agents/plugins/marketplace.json').read_text(encoding='utf-8'))
            plugin = (self.destination / catalog['plugins'][0]['source']['path']).resolve()
            self.installed = [p for p in self.installed if p['pluginId'] != tail[2]]
            self.installed.append({'pluginId': tail[2], 'name': 'freeup-space',
                                   'installed': True, 'enabled': not self.disabled,
                                   'version': json.loads((plugin / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))['version'],
                                   'source': {'source': 'local', 'path': str(plugin)}})
        elif tail[:2] == ['plugin', 'remove']:
            self.installed = [p for p in self.installed if p['pluginId'] != tail[2]]
        elif tail == ['--doctor']:
            data = {'ok': True, 'version': '1.2.3'}
        elif tail == ['--mcp']:
            requests = [json.loads(line) for line in kwargs['input'].splitlines()]
            replies = []
            for request in requests:
                if 'id' not in request:
                    continue
                result = ({'serverInfo': {'name': 'freeup-space'}, 'protocolVersion': '2025-06-18'}
                          if request['method'] == 'initialize' else
                          {'contents': [{'uri': 'ui://freeup-space/widget-v1.html',
                                         'mimeType': 'text/html;profile=mcp-app', 'text': '' if self.bad_resource else '<html>synthetic widget</html>'}]})
                replies.append(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}))
            return subprocess.CompletedProcess(args, 0, '\n'.join(replies), '')
        return subprocess.CompletedProcess(args, 0, json.dumps(data), '')


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'gettempdir', lambda: str(tmp_path))
    payload = tmp_path / 'payload'
    manifest = payload / 'plugin/.codex-plugin/plugin.json'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({'name': 'freeup-space', 'version': '1.2.3'}))
    (payload / 'plugin/.mcp.json').write_text('{"portable": true}')
    runtime = payload / 'runtime/freeup-space'
    runtime.parent.mkdir()
    runtime.write_text('synthetic')
    runtime.chmod(0o755)
    (runtime.parent / 'freeup-space.exe').write_text('synthetic')
    destination = tmp_path / 'installed ü space'
    host = Host(destination)
    monkeypatch.setattr(core.subprocess, 'run', host.run)
    codex = tmp_path / 'codex'
    codex.touch()
    return payload, destination, codex, host


def test_install_and_repair_keep_immutable_versions(fixture):
    payload, destination, codex, host = fixture
    first = core.install(payload, destination, codex)
    first_config = (Path(first['plugin_root']) / '.mcp.json').read_bytes()
    second = core.install(payload, destination, codex)
    assert first['version'] == '1.2.3'
    assert first['plugin_root'] != second['plugin_root']
    assert (Path(first['plugin_root']) / '.mcp.json').read_bytes() == first_config
    cfg = json.loads(first_config)['mcpServers']['freeup-space']
    assert Path(cfg['command']).is_absolute()
    assert Path(cfg['command']).is_file()
    assert cfg['args'] == ['--mcp'] and Path(cfg['cwd']) == first['plugin_root']
    assert json.loads((payload / 'plugin/.mcp.json').read_text(encoding='utf-8')) == {'portable': True}
    assert sum('--mcp' in call for call in host.calls) >= 2


def test_missing_host_does_not_create_destination(fixture):
    payload, destination, codex, _ = fixture
    with pytest.raises(core.SetupError, match='Codex'):
        core.install(payload, destination, codex.with_name('missing'))
    assert not destination.exists()


def test_unsupported_cli_does_not_create_destination(fixture):
    payload, destination, codex, host = fixture
    host.fail = '--help'
    with pytest.raises(core.SetupError):
        core.install(payload, destination, codex)
    assert not destination.exists()


def test_marketplace_name_collision_does_not_write(fixture, tmp_path):
    payload, destination, codex, host = fixture
    host.marketplaces = [{'name': 'freeup-space', 'root': str(tmp_path / 'unrelated')}]
    with pytest.raises(core.SetupError, match='marketplace'):
        core.install(payload, destination, codex)
    assert not destination.exists()


def test_unowned_catalog_is_never_replaced(fixture):
    payload, destination, codex, host = fixture
    catalog = destination / '.agents/plugins/marketplace.json'
    catalog.parent.mkdir(parents=True)
    catalog.write_text('{"name": "personal", "plugins": []}')
    before = catalog.read_bytes()
    with pytest.raises(core.SetupError):
        core.install(payload, destination, codex)
    assert catalog.read_bytes() == before


def test_failed_update_restores_catalog_and_keeps_old_version(fixture):
    payload, destination, codex, host = fixture
    first = core.install(payload, destination, codex)
    catalog = Path(first['marketplace_path'])
    before = catalog.read_bytes()
    host.fail = 'add'
    with pytest.raises(core.SetupError):
        core.install(payload, destination, codex)
    assert catalog.read_bytes() == before
    assert Path(first['plugin_root']).is_dir()


def test_disabled_install_is_not_success(fixture):
    payload, destination, codex, host = fixture
    host.disabled = True
    with pytest.raises(core.SetupError, match='enabled'):
        core.install(payload, destination, codex)


def test_migration_requires_exact_repository_and_success(fixture):
    payload, destination, codex, host = fixture
    def old(plugin_id, remote):
        return {'pluginId': plugin_id, 'name': 'freeup-space', 'installed': True,
                'marketplaceSource': {'sourceType': 'git', 'source': remote}}
    host.installed = [old('freeup-space@personal', 'git@github.com:raviasha/Freeup_Space.git'),
                      old('freeup-space@unrelated', 'https://github.com/other/Freeup_Space.git')]
    core.install(payload, destination, codex)
    removed = [call[3] for call in host.calls if call[1:3] == ['plugin', 'remove'] and '--help' not in call]
    assert removed == ['freeup-space@personal']
    verify_at = max(i for i, call in enumerate(host.calls) if '--mcp' in call)
    remove_at = next(i for i, call in enumerate(host.calls) if call[1:3] == ['plugin', 'remove'] and '--help' not in call)
    assert remove_at > verify_at


def test_doctor_failure_never_registers_marketplace(fixture):
    payload, destination, codex, host = fixture
    host.fail = '--doctor'
    with pytest.raises(core.SetupError):
        core.install(payload, destination, codex)
    assert not any('add' in call and '--help' not in call for call in host.calls)


def test_discovery_checks_path_and_mac_bundles(tmp_path, monkeypatch):
    monkeypatch.setattr(core.sys, 'platform', 'darwin')
    monkeypatch.setattr(core.Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(core, '_mac_application_roots', lambda: [tmp_path / 'Applications'])
    monkeypatch.setattr(core.shutil, 'which', lambda name: None)
    cli = tmp_path / 'Applications/Codex.app/Contents/Resources/codex'
    cli.parent.mkdir(parents=True)
    cli.touch()
    assert cli in core.discover_codex()


def test_default_root_is_per_user(tmp_path, monkeypatch):
    monkeypatch.setattr(core.sys, 'platform', 'win32')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    assert core.default_install_root().is_relative_to(tmp_path)


def test_install_stamps_native_cli_runtime_path(fixture):
    payload, destination, codex, host = fixture
    result = core.install(payload, destination, codex)
    plugin = Path(result['plugin_root'])
    command = json.loads((plugin / '.mcp.json').read_text(encoding='utf-8'))['mcpServers']['freeup-space']['command']
    assert (plugin / '.runtime-path').read_text(encoding='utf-8') == command + '\n'
    assert not (payload / 'plugin/.runtime-path').exists()


def test_frozen_windows_external_launch_restores_dll_directory(tmp_path, monkeypatch):
    dll_calls = []
    bundle = tmp_path / 'bundle'
    external = tmp_path / 'host'
    monkeypatch.setattr(core.sys, 'platform', 'win32')
    monkeypatch.setattr(core.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(core.sys, '_MEIPASS', str(bundle), raising=False)
    monkeypatch.setattr(core.ctypes, 'windll', SimpleNamespace(kernel32=SimpleNamespace(SetDllDirectoryW=dll_calls.append)), raising=False)
    monkeypatch.setenv('PATH', core.os.pathsep.join([str(bundle), str(external)]))
    monkeypatch.setenv('LD_LIBRARY_PATH', str(bundle))
    def run(args, **kwargs):
        assert dll_calls == [None]
        assert kwargs['env']['PATH'] == str(external)
        assert 'LD_LIBRARY_PATH' not in kwargs['env']
        raise subprocess.TimeoutExpired(args, 1)
    monkeypatch.setattr(core.subprocess, 'run', run)
    with pytest.raises(core.SetupError):
        core._run([external, '--version'])
    assert dll_calls == [None, str(bundle)]


def test_windows_discovery_uses_openai_package_locations(tmp_path, monkeypatch):
    local = tmp_path / 'local'
    packaged = tmp_path / 'package'
    cli = packaged / 'app/resources/codex.exe'
    cli.parent.mkdir(parents=True)
    cli.touch()
    monkeypatch.setattr(core.sys, 'platform', 'win32')
    monkeypatch.setenv('LOCALAPPDATA', str(local))
    monkeypatch.setattr(core.shutil, 'which', lambda name: 'powershell.exe' if name == 'powershell.exe' else None)
    def run(args, **kwargs):
        assert "'^OpenAI[.]'" in args[-1]
        return subprocess.CompletedProcess(args, 0, json.dumps([str(packaged)]), '')
    monkeypatch.setattr(core.subprocess, 'run', run)
    assert core.discover_codex() == [cli.resolve()]


def test_migration_is_not_attempted_after_failed_verification(fixture):
    payload, destination, codex, host = fixture
    host.installed = [{'pluginId': 'freeup-space@personal', 'name': 'freeup-space', 'installed': True,
                       'marketplaceSource': {'sourceType': 'git', 'source': 'https://github.com/raviasha/Freeup_Space.git'}}]
    host.disabled = True
    with pytest.raises(core.SetupError):
        core.install(payload, destination, codex)
    assert not any('remove' in call and 'freeup-space@personal' in call for call in host.calls)


def test_empty_widget_resource_is_not_success(fixture):
    payload, destination, codex, host = fixture
    host.bad_resource = True
    with pytest.raises(core.SetupError, match='widget resource'):
        core.install(payload, destination, codex)
    assert not any('add' in call and '--help' not in call for call in host.calls)


def test_copy_preserves_runtime_bundle_symlinks(fixture):
    payload, destination, codex, host = fixture
    library = payload / 'runtime/_internal/library'
    library.parent.mkdir()
    library.write_bytes(b'synthetic library')
    link = library.with_name('library-link')
    try:
        link.symlink_to('library')
    except OSError:
        pytest.skip('This Windows environment cannot create test symlinks')
    result = core.install(payload, destination, codex)
    installed_link = Path(result['plugin_root']).parent / 'runtime/_internal/library-link'
    assert installed_link.is_symlink()
    assert installed_link.read_bytes() == b'synthetic library'


def test_repair_busts_codex_cache_while_doctor_keeps_payload_version(fixture):
    payload, destination, codex, host = fixture
    first = core.install(payload, destination, codex)
    second = core.install(payload, destination, codex)
    first_manifest = json.loads((Path(first['plugin_root']) / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))
    second_manifest = json.loads((Path(second['plugin_root']) / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))
    assert first_manifest['version'] != second_manifest['version']
    assert first_manifest['version'] == first['installed_version']
    assert second_manifest['version'] == second['installed_version']
    assert first['version'] == second['version'] == '1.2.3'
    assert host.installed[-1]['version'] == second['installed_version']
    assert json.loads((payload / 'plugin/.codex-plugin/plugin.json').read_text(encoding='utf-8'))['version'] == '1.2.3'


def test_failed_plugin_rollback_does_not_skip_marketplace_rollback(fixture):
    payload, destination, codex, host = fixture
    host.fail_commands = {
        ('plugin', 'add', core.PLUGIN_ID, '--json'): 'plugin add failed',
        ('plugin', 'remove', core.PLUGIN_ID, '--json'): 'plugin not installed',
    }
    with pytest.raises(core.SetupError, match='plugin not installed'):
        core.install(payload, destination, codex)
    assert host.marketplaces == []
    assert any(call[1:] == ['plugin', 'marketplace', 'remove', 'freeup-space', '--json'] for call in host.calls)
    assert not (destination / '.agents/plugins/marketplace.json').exists()


def test_failed_migration_is_returned_as_warning_and_keeps_new_install(fixture):
    payload, destination, codex, host = fixture
    old_id = 'freeup-space@personal'
    host.installed = [{'pluginId': old_id, 'name': 'freeup-space', 'installed': True,
                       'marketplaceSource': {'sourceType': 'git', 'source': 'https://github.com/raviasha/Freeup_Space.git'}}]
    host.fail_commands = {('plugin', 'remove', old_id, '--json'): 'old plugin removal failed'}
    result = core.install(payload, destination, codex)
    assert len(result['warnings']) == 1
    assert old_id in result['warnings'][0]
    assert 'old plugin removal failed' in result['warnings'][0]
    assert {p['pluginId'] for p in host.installed} == {old_id, core.PLUGIN_ID}
    assert Path(result['plugin_root']).is_dir()


def test_same_destination_cannot_run_two_setups(fixture):
    payload, destination, codex, host = fixture
    rejected = []
    def progress(message):
        if not rejected:
            calls_before = len(host.calls)
            with pytest.raises(core.SetupError, match='another setup is running'):
                core.install(payload, destination, codex)
            assert len(host.calls) == calls_before
            rejected.append(True)
    core.install(payload, destination, codex, progress)
    assert rejected == [True]


def test_failed_setup_releases_destination_lock(fixture):
    payload, destination, codex, host = fixture
    host.fail = '--help'
    with pytest.raises(core.SetupError):
        core.install(payload, destination, codex)
    host.fail = None
    result = core.install(payload, destination, codex)
    assert result['version'] == '1.2.3'


def test_nonempty_unowned_destination_is_not_adopted(fixture):
    payload, destination, codex, host = fixture
    destination.mkdir()
    unrelated = destination / 'keep.txt'
    unrelated.write_text('unrelated document', encoding='utf-8')
    with pytest.raises(core.SetupError, match='empty folder'):
        core.install(payload, destination, codex)
    assert list(destination.iterdir()) == [unrelated]
    assert unrelated.read_text(encoding='utf-8') == 'unrelated document'
    assert not any('add' in call and '--help' not in call for call in host.calls)


def test_failed_staging_keeps_ownership_for_repair(fixture):
    payload, destination, codex, host = fixture
    host.fail = '--doctor'
    with pytest.raises(core.SetupError):
        core.install(payload, destination, codex)
    assert (destination / core.MARKER).exists()
    host.fail = None
    result = core.install(payload, destination, codex)
    assert result['version'] == '1.2.3'


def test_selection_skips_access_denied_launcher_and_uses_working_cli(tmp_path, monkeypatch):
    denied = tmp_path / 'WindowsApps/codex.exe'
    usable = tmp_path / 'Codex/app/resources/codex.exe'
    for path in (denied, usable):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    def run(args, **kwargs):
        if Path(args[0]) == denied:
            raise PermissionError(13, '[WinError 5] Access is denied', str(denied))
        output = 'codex-cli 0.153.4' if args[1:] == ['--version'] else 'Usage: plugin command --json'
        return subprocess.CompletedProcess(args, 0, output, '')
    monkeypatch.setattr(core.subprocess, 'run', run)
    messages = []
    assert core.select_codex([denied, usable], progress=messages.append) == usable
    assert any(str(denied) in message and 'denied' in message for message in messages)


@pytest.mark.parametrize('response', ['', 'Codex Desktop 1.0', 'codex-cli 0.153.4'])
def test_selection_rejects_gui_and_unsupported_cli(tmp_path, monkeypatch, response):
    launcher = tmp_path / 'codex.exe'
    launcher.touch()
    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, response if '--version' in args else 'Unsupported command', '')
    monkeypatch.setattr(core.subprocess, 'run', run)
    with pytest.raises(core.SetupError, match='usable Codex'):
        core.select_codex([launcher])


def test_selection_skips_unresponsive_launcher(tmp_path, monkeypatch):
    paths = [tmp_path / name for name in ('hung', 'working')]
    for path in paths:
        path.touch()
    def run(args, **kwargs):
        if Path(args[0]) == paths[0]:
            raise subprocess.TimeoutExpired(args, kwargs['timeout'])
        return subprocess.CompletedProcess(args, 0, 'codex-cli 0.153.4' if '--version' in args else '--json', '')
    monkeypatch.setattr(core.subprocess, 'run', run)
    assert core.select_codex(paths) == paths[1]


def test_windows_discovery_prefers_bundled_cli_over_path_alias(tmp_path, monkeypatch):
    alias = tmp_path / 'WindowsApps/codex.exe'
    bundled = tmp_path / 'Programs/Codex/resources/codex.exe'
    for path in (alias, bundled):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr(core.sys, 'platform', 'win32')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setattr(core.shutil, 'which', lambda name: str(alias) if name == 'codex' else None)
    assert core.discover_codex()[0] == bundled


def test_install_rejects_desktop_executable_before_writing(fixture, monkeypatch):
    payload, destination, codex, host = fixture
    original = host.run
    def run(args, **kwargs):
        if args[1:] == ['--version']:
            return subprocess.CompletedProcess(args, 0, '', '')
        return original(args, **kwargs)
    monkeypatch.setattr(core.subprocess, 'run', run)
    with pytest.raises(core.SetupError, match='CLI'):
        core.install(payload, destination, codex)
    assert not destination.exists()


def test_widget_startup_directory_survives_codex_cache_eviction(fixture, tmp_path):
    import shutil
    payload, destination, codex, _ = fixture
    result = core.install(payload, destination, codex)
    source = Path(result['plugin_root'])
    cache = tmp_path / 'codex-cache' / result['installed_version']
    shutil.copytree(source, cache)
    cfg = json.loads((cache / '.mcp.json').read_text(encoding='utf-8'))['mcpServers']['freeup-space']
    launch_directory = cache / cfg['cwd']
    shutil.rmtree(cache)  # Synthetic cache only: model Codex replacing a plugin cache.
    assert Path(cfg['command']).is_file()
    assert launch_directory.is_dir(), 'An old task must remain startable after its cache is replaced'


def test_runtime_checks_use_the_registered_working_directory(fixture, monkeypatch):
    payload, destination, codex, host = fixture
    directories = []
    def run(args, **kwargs):
        if '--doctor' in args or '--mcp' in args:
            directories.append(kwargs.get('cwd'))
        return host.run(args, **kwargs)
    monkeypatch.setattr(core.subprocess, 'run', run)
    result = core.install(payload, destination, codex)
    assert len(directories) == 4
    assert all(directory is not None and Path(directory) == result['plugin_root'] for directory in directories)
