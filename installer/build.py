"""Build self-contained installers on the OS/architecture they target."""
import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from installer.bundle import pack_payload
from installer.health import smoke_runtime

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/freeup-space'


def stage_payload(plugin: Path, runtime: Path, destination: Path) -> None:
    destination.mkdir(parents=True)
    target = destination / 'plugin'
    target.mkdir()
    for name in ('.codex-plugin', '.mcp.json', 'assets', 'src', 'scripts', 'skills', 'docs', 'README.md', 'LICENSE'):
        source = plugin / name
        if source.is_dir():
            shutil.copytree(source, target / name, symlinks=True,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        elif source.is_file():
            shutil.copy2(source, target / name)
    shutil.copytree(runtime, destination / 'runtime', symlinks=True)


def run(command, **kwargs):
    subprocess.run([str(part) for part in command], check=True, **kwargs)




def build(output: Path, runtime_only=False) -> Path:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    common = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--noupx',
              '--distpath', output / 'dist', '--workpath', output / 'work', '--specpath', output]
    signing = []
    if sys.platform == 'darwin' and os.environ.get('MACOS_CODESIGN_IDENTITY'):
        signing = ['--codesign-identity', os.environ['MACOS_CODESIGN_IDENTITY']]
    run([*common, '--name', 'freeup-space', '--onedir', '--console',
         '--paths', PLUGIN / 'src', '--add-data', f'{PLUGIN / "assets"}:plugin/assets',
         '--add-data', f'{PLUGIN / ".codex-plugin"}:plugin/.codex-plugin',
         *signing, ROOT / 'installer/runtime_entry.py'])
    runtime = output / 'dist/freeup-space'
    executable = runtime / ('freeup-space.exe' if os.name == 'nt' else 'freeup-space')
    report = smoke_runtime(executable)
    (output / 'runtime-check.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    if runtime_only:
        return executable
    payload = output / 'payload'
    if payload.exists():
        shutil.rmtree(payload)  # This function owns only the build output's payload directory.
    stage_payload(PLUGIN, runtime, payload)
    archive = output / 'payload.tar.gz'
    pack_payload(payload, archive)
    kind = ['--onefile'] if os.name == 'nt' else ['--onedir', '--osx-bundle-identifier', 'com.raviasha.freeupspace.setup']
    run([*common, '--name', 'Freeup Space Setup', '--windowed', *kind,
         '--paths', ROOT, '--add-data', f'{archive}:.', *signing, ROOT / 'installer/setup.py'])
    if os.name == 'nt':
        artifact = output / 'Freeup-Space-Windows-x64.exe'
        shutil.copy2(output / 'dist/Freeup Space Setup.exe', artifact)
        setup_executable = artifact
    elif sys.platform == 'darwin':
        app = output / 'dist/Freeup Space Setup.app'
        setup_executable = app / 'Contents/MacOS/Freeup Space Setup'
        arch = 'Apple-Silicon' if platform.machine() == 'arm64' else 'Intel'
        artifact = output / f'Freeup-Space-macOS-{arch}.zip'
        run(['/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', app, artifact])
    else:
        raise RuntimeError('Installer packages are supported on Windows and macOS only')
    smoke_report = output / 'setup-check.json'
    run([setup_executable, '--smoke-test', smoke_report], timeout=120)
    if not json.loads(smoke_report.read_text(encoding='utf-8')).get('ok'):
        raise RuntimeError(f'Setup smoke test failed: {smoke_report.read_text()}')
    checksum = hashlib.sha256(artifact.read_bytes()).hexdigest()
    artifact.with_suffix(artifact.suffix+'.sha256').write_text(f'{checksum}  {artifact.name}\n', encoding='utf-8')
    print(artifact, flush=True)
    return artifact


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/setup')
    parser.add_argument('--runtime-only', action='store_true')
    args = parser.parse_args()
    build(args.output, args.runtime_only)
