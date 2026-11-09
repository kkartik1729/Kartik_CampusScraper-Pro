"""Generated local service settings must be safe to rerun on Windows/Linux."""
from pathlib import Path
import importlib.util

def test_setup_search_preserves_existing_configuration(tmp_path):
    source = Path(__file__).resolve().parents[1] / "tools" / "setup_search.py"
    spec = importlib.util.spec_from_file_location("setup_search", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main(tmp_path)
    path = tmp_path / "searxng" / "settings.yml"
    original = path.read_text()
    assert "formats: [html, json]" in original
    assert "secret_key:" in original and "limiter: false" in original
    module.main(tmp_path)
    assert path.read_text() == original
    assert (tmp_path / "searxng" / "data").is_dir()


def test_compose_search_binds_only_localhost():
    root = Path(__file__).resolve().parents[1]
    config = (root / "compose.search.yml").read_text()
    assert '"127.0.0.1:8080:8080"' in config
    assert "settings.yml:ro" in config
    assert "mem_limit:" in config and "cpus:" in config


def test_search_upgrade_preserves_secret_and_backup_and_is_idempotent(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'tools' / 'setup_search.py'
    spec = importlib.util.spec_from_file_location('upgrade_search', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main(tmp_path)
    path = tmp_path / 'searxng' / 'settings.yml'
    original = path.read_text().replace('[google, duckduckgo, brave, startpage, yahoo, qwant]', '[google, bing, duckduckgo, brave]')
    for name in ('startpage', 'yahoo', 'qwant'):
        original = original.replace(f'  - name: {name}\n    disabled: false\n', '')
    original += '  - name: bing\n    disabled: false\n'
    path.write_text(original)
    module.main(tmp_path, upgrade=True)
    updated = path.read_text()
    assert 'name: bing' not in updated
    assert all(f'name: {n}' in updated for n in ('startpage', 'yahoo', 'qwant'))
    assert next(x for x in original.splitlines() if 'secret_key' in x) in updated
    assert path.with_suffix('.yml.before-recovery').read_text() == original
    module.main(tmp_path, upgrade=True)
    assert path.read_text() == updated


def test_search_upgrade_refuses_unknown_custom_layout(tmp_path):
    import pytest
    source = Path(__file__).resolve().parents[1] / 'tools' / 'setup_search.py'
    spec = importlib.util.spec_from_file_location('custom_search', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    folder = tmp_path / 'searxng'
    folder.mkdir()
    path = folder / 'settings.yml'
    path.write_text('use_default_settings: true\n')
    with pytest.raises(SystemExit, match='Custom'):
        module.main(tmp_path, upgrade=True)
    assert path.read_text() == 'use_default_settings: true\n'
