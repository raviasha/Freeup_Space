import io
import tarfile
from pathlib import Path

import pytest

from installer.bundle import extract_payload, pack_payload


def test_payload_round_trip_keeps_runtime_executable_and_symlinks(tmp_path):
    source = tmp_path / 'payload'
    (source / 'runtime').mkdir(parents=True)
    executable = source / 'runtime' / 'freeup-space'
    executable.write_bytes(b'bundled-runtime')
    executable.chmod(0o755)
    (source / 'plugin').mkdir()
    (source / 'plugin' / 'README.md').write_text('plugin')
    if __import__('os').name != 'nt':
        (source / 'runtime' / 'alias').symlink_to('freeup-space')
    archive = tmp_path / 'payload.tar.gz'
    pack_payload(source, archive)
    destination = tmp_path / 'extracted'
    extract_payload(archive, destination)
    assert (destination / 'runtime/freeup-space').read_bytes() == b'bundled-runtime'
    assert (destination / 'runtime/freeup-space').stat().st_mode & 0o111
    if __import__('os').name != 'nt':
        assert (destination / 'runtime/alias').is_symlink()


@pytest.mark.parametrize('name,link', [('../escape', None), ('/escape', None), ('runtime/link', '../../escape')])
def test_extraction_rejects_paths_outside_payload(tmp_path, name, link):
    archive = tmp_path / 'bad.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        member = tarfile.TarInfo(name)
        if link:
            member.type = tarfile.SYMTYPE
            member.linkname = link
            tar.addfile(member)
        else:
            member.size = 1
            tar.addfile(member, io.BytesIO(b'x'))
    with pytest.raises(ValueError):
        extract_payload(archive, tmp_path / 'destination')
    assert not (tmp_path / 'escape').exists()
