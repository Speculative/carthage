from pathlib import Path

import pytest

from carthage.personal_config import load_personal_config
from carthage.web import WebAccess, parse_web


def test_web_personal_config_and_overlay(tmp_path):
    token = tmp_path / 'token'
    token.write_text('not-a-real-token')
    config = tmp_path / 'config.toml'
    config.write_text(f'''[carthage]
schema_version = 1
[web]
server_addr = "tunnel.example.com"
domain = "agents.example.com"
token_file = "{token}"
supervise = true
''')
    result = load_personal_config(config)
    assert not result.warnings
    overlay = result.config.web.runtime_overlay('test-project')
    assert {e.name: e.value for e in overlay.environment} == {
        'CARTHAGE_WEB_HOST': 'test-project.agents.example.com',
        'CARTHAGE_WEB_SUPERVISE': '1',
    }
    assert overlay.mounts[0].target == "/run/carthage-web"
    assert not overlay.mounts[0].read_only
    assert 'not-a-real-token' not in repr(overlay)


@pytest.mark.parametrize('raw', [False, {}, {'server_addr': 'x', 'domain': '*.example.com', 'token_file': '/tmp/token'}, {'server_addr': 'x', 'domain': 'example.com', 'token_file': 'relative'}])
def test_invalid_config_disables_web(raw):
    config, warnings = parse_web(raw, Path('/config.toml'))
    assert config is None
    assert warnings


def test_missing_token_and_invalid_slug(tmp_path):
    config = WebAccess('tunnel.example.com', 'agents.example.com', str(tmp_path / 'missing'))
    with pytest.raises(ValueError, match='token file'):
        config.runtime_overlay('demo')
    with pytest.raises(ValueError, match='DNS-compatible'):
        config.runtime_overlay('unsafe.name')


def test_sidecar_isolates_token_and_writes_private_config(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    token = tmp_path / "token"
    token.write_text("fake-secret")
    config = WebAccess("tunnel.example.com", "agents.example.com", str(token))
    config.prepare("demo")
    directory = config.tunnel_dir("demo")
    assert directory.stat().st_mode & 0o777 == 0o700
    assert (directory / "frpc.json").stat().st_mode & 0o777 == 0o600
    rendered = json.loads((directory / "frpc.json").read_text())
    assert rendered["proxies"][0]["plugin"]["unixPath"] == "/run/carthage-web/router.sock"
    assert "fake-secret" not in json.dumps(rendered)
    sidecar = config.sidecar("demo", "dev")["carthage-frpc"]
    assert sidecar["cap_drop"] == ["ALL"]
    assert sidecar["read_only"]
    assert len(sidecar["volumes"]) == 3
    assert all(mount["read_only"] for mount in sidecar["volumes"])
    main = config.runtime_overlay("demo")
    assert all(mount.source != str(token) for mount in main.mounts)
    assert str(directory / "frpc.json") not in repr(main)
