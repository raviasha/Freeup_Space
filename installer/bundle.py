"""Preserve frozen runtime permissions and links inside the embedded payload."""
import tarfile
from pathlib import Path, PurePosixPath


def pack_payload(source: Path, archive: Path) -> None:
    with tarfile.open(archive, 'w:gz') as tar:
        for child in sorted(source.iterdir()):
            tar.add(child, arcname=child.name)


def extract_payload(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar.getmembers():
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or '\\' in member.name:
                raise ValueError('Invalid setup payload path')
            if not (member.isfile() or member.isdir() or member.issym()):
                raise ValueError('Unsupported setup payload entry')
            target = root.joinpath(*name.parts)
            if member.issym():
                link = PurePosixPath(member.linkname)
                if link.is_absolute() or '\\' in member.linkname:
                    raise ValueError('Invalid setup payload link')
                target = target.parent.joinpath(*link.parts)
            try:
                target.resolve().relative_to(root)
            except ValueError:
                raise ValueError('Setup payload points outside its directory')
        # data_filter also catches traversal through symlinks created during extraction.
        if hasattr(tarfile, 'data_filter'):
            tar.extractall(root, filter='data')
        else:
            raise ValueError('Building/running setup requires Python 3.12 or newer')
