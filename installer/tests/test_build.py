import os
from pathlib import Path

from installer.build import stage_payload


def test_staged_payload_omits_local_state_and_preserves_source_launcher(tmp_path):
    plugin = tmp_path / 'plugin'
    plugin.mkdir()
    for name in ('assets', 'src', 'scripts', 'skills', '.codex-plugin', '.venv', '.freeup-space', 'tests'):
        (plugin / name).mkdir()
        (plugin / name / 'example').write_text(name)
    (plugin / '.mcp.json').write_text('{"command":"python3"}')
    (plugin / 'README.md').write_text('readme')
    runtime = tmp_path / 'engine'
    runtime.mkdir()
    executable = runtime / 'freeup-space'
    executable.write_bytes(b'executable')
    executable.chmod(0o755)
    payload = tmp_path / 'payload'
    stage_payload(plugin, runtime, payload)
    assert (payload / 'plugin/.mcp.json').read_text() == '{"command":"python3"}'
    assert (payload / 'plugin/assets/example').read_text() == 'assets'
    assert not (payload / 'plugin/.venv').exists()
    assert not (payload / 'plugin/.freeup-space').exists()
    assert not (payload / 'plugin/tests').exists()
    assert (payload / 'runtime/freeup-space').read_bytes() == b'executable'
    if os.name != 'nt':
        assert (payload / 'runtime/freeup-space').stat().st_mode & 0o111
