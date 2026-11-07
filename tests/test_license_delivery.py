import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_script(filename):
    spec = importlib.util.spec_from_file_location(
        filename.replace('-', '_'), ROOT / 'scripts' / filename
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_project_license_evidence_is_intact():
    check = load_script('check-licenses.py')
    assert check.check(ROOT) == []


def test_release_check_fails_closed_for_unresolved_review(tmp_path):
    check = load_script('check-licenses.py')
    # Use real evidence with only the review record isolated so future resolution
    # does not make this test depend on today's outstanding issues.
    import shutil
    shutil.copytree(ROOT / 'docs/licenses', tmp_path / 'docs/licenses')
    for name in ('LICENSE', 'NOTICE.md', 'CONTRIBUTING.md', 'pyproject.toml'):
        shutil.copy2(ROOT / name, tmp_path / name)
    review_path = tmp_path / 'docs/licenses/release-review.json'
    review = json.loads(review_path.read_text())
    for issue in review['issues']:
        issue['status'] = 'resolved'
        issue['resolution'] = 'Synthetic test evidence, not a real clearance.'
    review['issues'][0]['status'] = 'blocked'
    review_path.write_text(json.dumps(review))
    errors = check.check(tmp_path, release=True)
    assert len(errors) == 1
    assert errors[0].startswith('Release blocked [ffmpeg-corresponding-source]')
    assert check.check(tmp_path) == []
    review['issues'][0]['status'] = 'resolved'
    review_path.write_text(json.dumps(review))
    assert check.check(tmp_path, release=True) == []


def test_notice_collection_preserves_text_and_reports_missing(tmp_path):
    collector = load_script('collect_licenses.py')
    site = tmp_path / 'site'
    metadata = site / 'example-1.0.dist-info'
    metadata.mkdir(parents=True)
    (metadata / 'METADATA').write_text(
        'Metadata-Version: 2.4\nName: example\nVersion: 1.0\nLicense-Expression: MIT\n'
        'License-File: special/terms.data\n'
    )
    (metadata / 'LICENSE').write_text('Copyright Example; synthetic test license.\n')
    extra = metadata / 'licenses/numpy/dragon4_LICENSE.txt'
    extra.parent.mkdir(parents=True)
    extra.write_text('Additional bundled notice.\n')
    declared = metadata / 'special/terms.data'
    declared.parent.mkdir()
    declared.write_text('Declared license with a nonstandard filename.\n')
    (metadata / 'direct_url.json').write_text('{"url": "file:///private/developer/path"}')
    (metadata / 'RECORD').write_text(
        'example-1.0.dist-info/LICENSE,,\nexample-1.0.dist-info/direct_url.json,,\n'
        'example-1.0.dist-info/licenses/numpy/dragon4_LICENSE.txt,,\n'
        'example-1.0.dist-info/special/terms.data,,\n'
    )
    empty = site / 'missing-2.0.dist-info'
    empty.mkdir()
    (empty / 'METADATA').write_text('Metadata-Version: 2.4\nName: missing\nVersion: 2.0\n')
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'LICENSE.txt').write_text('Synthetic Python license.\n')
    output = tmp_path / 'Licenses'
    report = collector.collect(output, site, runtime=runtime, scope='Synthetic test')
    assert report['dependencies'][0]['name'] == 'example'
    assert not report['dependencies'][0]['review_required']
    assert len(report['dependencies'][0]['notices']) == 3
    assert report['dependencies'][1]['review_required']
    assert (output / 'Python-LICENSE.txt').read_text() == 'Synthetic Python license.\n'
    assert not list(output.rglob('direct_url.json'))
    assert str(tmp_path) not in (output / 'dependencies.json').read_text()
    copied = output / report['dependencies'][0]['notices'][0]['file']
    assert copied.read_bytes() == (metadata / 'LICENSE').read_bytes()


def test_model_evidence_detects_corruption(tmp_path):
    import shutil
    check = load_script('check-licenses.py')
    shutil.copytree(ROOT / 'docs/licenses', tmp_path / 'docs/licenses')
    for name in ('LICENSE', 'NOTICE.md', 'CONTRIBUTING.md', 'pyproject.toml'):
        shutil.copy2(ROOT / name, tmp_path / name)
    (tmp_path / 'docs/licenses/model-cards/windows-qwen-README.txt').write_text('changed')
    assert any('Changed upstream model evidence' in error for error in check.check(tmp_path))


def test_empty_model_manifest_is_rejected(tmp_path):
    import shutil
    check = load_script('check-licenses.py')
    shutil.copytree(ROOT / 'docs/licenses', tmp_path / 'docs/licenses')
    for name in ('LICENSE', 'NOTICE.md', 'CONTRIBUTING.md', 'pyproject.toml'):
        shutil.copy2(ROOT / name, tmp_path / name)
    (tmp_path / 'docs/licenses/model-cards/manifest.json').write_text('[]')
    assert any('must retain all configured model' in error for error in check.check(tmp_path))


def test_frozen_pystray_checks_source_archive_and_notice_bytes(tmp_path, monkeypatch):
    import hashlib
    import sys
    from types import SimpleNamespace

    import pytest

    check = load_script('check_windows_licenses.py')
    modules = {'unrelated': (0, 1, 2)}
    reader = SimpleNamespace(
        toc={'PYZ.pyz': ()},
        open_embedded_archive=lambda name: SimpleNamespace(toc=modules),
    )
    monkeypatch.setitem(sys.modules, 'PyInstaller.archive.readers',
                        SimpleNamespace(CArchiveReader=lambda path: reader))
    source = tmp_path / '_internal/pystray'
    source.mkdir(parents=True)
    for name in ('__init__.py', '_base.py', '_win32.py'):
        (source / name).write_text('# Synthetic source\n')
    licenses = tmp_path / 'Licenses'
    licenses.mkdir()
    notice = licenses / 'pystray-COPYING'
    notice.write_text('Synthetic upstream notice\n')
    report = {'dependencies': [{'name': 'pystray', 'notices': [{
        'file': notice.name, 'sha256': hashlib.sha256(notice.read_bytes()).hexdigest(),
    }]}]}
    (licenses / 'dependencies.json').write_text(json.dumps(report))
    for name in ('GPL-3.0.txt', 'LGPL-3.0.txt', 'Python-LICENSE.txt'):
        (licenses / name).write_text('Synthetic license\n')
    check.check(tmp_path)
    modules['pystray._base'] = (0, 1, 2)
    with pytest.raises(RuntimeError, match='embedded in PYZ'):
        check.check(tmp_path)
    modules.pop('pystray._base')
    (source / '_base.py').unlink()
    with pytest.raises(RuntimeError, match='Missing external'):
        check.check(tmp_path)
    (source / '_base.py').write_text('# Synthetic source\n')
    notice.write_text('Changed')
    with pytest.raises(RuntimeError, match='Changed upstream'):
        check.check(tmp_path)
    notice.unlink()
    with pytest.raises(RuntimeError, match='Missing or invalid upstream'):
        check.check(tmp_path)
