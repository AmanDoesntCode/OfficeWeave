"""Create and verify the portable Windows release using only the standard library."""

from pathlib import Path
import argparse
import re
from zipfile import ZIP_DEFLATED, ZipFile


VERSION = r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)'


def version_tuple(value):
    if not re.fullmatch(VERSION, value):
        raise ValueError('Use a version such as 1.0.1 (major.minor.patch).')
    return tuple(map(int, value.split('.')))


def current_version(root):
    source = (root / 'mail_merger' / '__init__.py').read_text('utf-8')
    project = (root / 'pyproject.toml').read_text('utf-8')
    version = re.search(r'^__version__ = "([^"]+)"', source, re.M)[1]
    if re.search(r'^version = "([^"]+)"', project, re.M)[1] != version:
        raise ValueError('Application and project versions must match.')
    version_tuple(version)
    return version


def release_name(version):
    version_tuple(version)
    return f'Mail-Merger-v{version}-Windows-x64'


def prepare_release(root, requested=None):
    current = version_tuple(current_version(root))
    existing = []
    for path in (root / 'dist').glob('Mail-Merger-v*-Windows-x64*'):
        match = re.fullmatch(r'Mail-Merger-v(\d+\.\d+\.\d+)-Windows-x64(?:\.zip)?', path.name)
        if match:
            existing.append(version_tuple(match[1]))
    if requested:
        selected = version_tuple(requested)
        if selected < current or (existing and selected <= max(existing)):
            raise ValueError('Choose a version newer than existing releases and no earlier than the source version.')
    else:
        selected = max([current, *existing])
        if selected in existing:
            selected = (*selected[:2], selected[2] + 1)
    version = '.'.join(map(str, selected))
    for relative, pattern in [('mail_merger/__init__.py', r'^__version__ = "[^"]+"'),
                              ('pyproject.toml', r'^version = "[^"]+"')]:
        path = root / relative
        prefix = '__version__' if relative.endswith('__init__.py') else 'version'
        content = re.sub(pattern, f'{prefix} = "{version}"', path.read_text('utf-8'), count=1, flags=re.M)
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(content, encoding='utf-8')
        temporary.replace(path)
    return version


def package_distribution(root, distribution):
    distribution = distribution.resolve()
    name = release_name(current_version(root))
    if distribution.parent != (root / 'dist').resolve() or distribution.name != name:
        raise ValueError('Expected the current versioned release folder directly inside dist.')
    if not (distribution / "Mail-Merger.exe").is_file():
        raise ValueError("Build the desktop executable first.")
    target = root / 'dist' / (name + '.zip')
    if target.exists():
        raise ValueError('This release ZIP already exists. Build a newer version instead of replacing it.')
    temporary = target.with_suffix(".zip.tmp")
    with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(distribution.rglob("*")):
            archive.write(path, path.relative_to(distribution.parent))
    with ZipFile(temporary) as archive:
        corrupt = archive.testzip()
        if corrupt:
            raise ValueError(f"Archive verification failed: {corrupt}")
    temporary.replace(target)
    return target


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--distribution', type=Path)
    parser.add_argument('--prepare', action='store_true', help='Choose the next unused version and sync source metadata.')
    parser.add_argument('--version', help='Explicit version for --prepare, for example 1.1.0.')
    args = parser.parse_args()
    try:
        if args.prepare:
            print(prepare_release(root, args.version))
        else:
            if args.version:
                parser.error('--version requires --prepare')
            distribution = args.distribution or root / 'dist' / release_name(current_version(root))
            target = package_distribution(root, distribution)
            print(f"Verified portable release: {target.name} ({target.stat().st_size:,} bytes)")
    except ValueError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
