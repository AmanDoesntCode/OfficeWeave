from zipfile import ZipFile

import pytest

from tools.package_release import current_version, package_distribution, prepare_release, release_name


@pytest.fixture
def release_root(tmp_path):
    (tmp_path / 'mail_merger').mkdir()
    (tmp_path / 'mail_merger/__init__.py').write_text('__version__ = "1.0.1"\n')
    (tmp_path / 'pyproject.toml').write_text('[project]\nversion = "1.0.1"\n')
    (tmp_path / 'dist').mkdir()
    return tmp_path


def test_next_release_increments_without_touching_previous_artifacts(release_root):
    root = release_root
    previous = root / 'dist' / release_name('1.0.1')
    previous.mkdir()
    (previous / 'Mail-Merger.exe').write_bytes(b'old executable')
    archive = root / 'dist' / (previous.name + '.zip')
    archive.write_bytes(b'old archive')
    assert prepare_release(root) == '1.0.2'
    assert current_version(root) == '1.0.2'
    assert (previous / 'Mail-Merger.exe').read_bytes() == b'old executable'
    assert archive.read_bytes() == b'old archive'


@pytest.mark.parametrize('version', ['1.0.0', '1.0.1', '../outside', '2.0', '01.0.0'])
def test_explicit_version_rejects_old_or_invalid_releases(release_root, version):
    (release_root / 'dist' / (release_name('1.0.1') + '.zip')).write_bytes(b'old')
    with pytest.raises(ValueError):
        prepare_release(release_root, version)
    assert current_version(release_root) == '1.0.1'


def test_matching_folder_zip_and_internal_root_are_versioned(release_root):
    root = release_root
    assert prepare_release(root, '1.1.0') == '1.1.0'
    folder = root / 'dist' / release_name('1.1.0')
    folder.mkdir()
    (folder / 'Mail-Merger.exe').write_bytes(b'executable')
    (folder / '_internal').mkdir()
    (folder / '_internal/asset.txt').write_text('asset')
    archive = package_distribution(root, folder)
    assert archive.stem == folder.name
    with ZipFile(archive) as zipped:
        assert zipped.testzip() is None
        assert zipped.read(folder.name + '/Mail-Merger.exe') == b'executable'
        assert zipped.read(folder.name + '/_internal/asset.txt') == b'asset'
    previous = archive.read_bytes()
    with pytest.raises(ValueError, match='already exists'):
        package_distribution(root, folder)
    assert archive.read_bytes() == previous
    assert prepare_release(root) == '1.1.1'


def test_unversioned_folder_and_metadata_mismatch_are_rejected(release_root):
    with pytest.raises(ValueError, match='versioned release folder'):
        package_distribution(release_root, release_root / 'dist/Mail-Merger')
    (release_root / 'pyproject.toml').write_text('[project]\nversion = "2.0.0"\n')
    with pytest.raises(ValueError, match='versions must match'):
        prepare_release(release_root)
