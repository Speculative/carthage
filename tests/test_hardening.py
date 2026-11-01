from carthage import daemon
from carthage.commands.up import _port_compose_override_content
from carthage.ports import HostPortBinding


def test_rootless_ids_map_writable_bind_mounts_to_host_user(monkeypatch):
    monkeypatch.setattr(daemon, 'docker_security_options', lambda: ('name=rootless',))
    assert daemon.runtime_ids() == (0, 0)
    monkeypatch.setattr(daemon, 'docker_security_options', lambda: ('name=seccomp',))
    monkeypatch.setattr(daemon.os, 'getuid', lambda: 1234)
    monkeypatch.setattr(daemon.os, 'getgid', lambda: 5678)
    assert daemon.runtime_ids() == (1234, 5678)


def test_all_services_publish_only_to_loopback(monkeypatch):
    from types import SimpleNamespace
    from carthage import ports
    cfg = SimpleNamespace(service_name='dev')
    monkeypatch.setattr(ports, 'extract_host_ports', lambda cfg, **kwargs: [
        HostPortBinding('dev', 5173, 5173, 'tcp', '0.0.0.0'),
        HostPortBinding('db', 5432, 5432, 'tcp', '::'),
        HostPortBinding('dev', 5353, 5353, 'udp'),
        HostPortBinding('dev', None, 3000, 'tcp'),
    ])
    content = _port_compose_override_content(cfg, False, [(5174, 5173), (9000, 9001)])
    assert '127.0.0.1:5174:5173/tcp' in content
    assert '127.0.0.1:5432:5432/tcp' in content
    assert '127.0.0.1:5353:5353/udp' in content
    assert '127.0.0.1::3000/tcp' in content
    assert '127.0.0.1:9000:9001/tcp' in content
    reset = _port_compose_override_content(cfg, True, [])
    assert reset.count('!reset []') == 2


def test_rootless_personal_image_preserves_home_and_unattended_agents(monkeypatch):
    from carthage import personal_image
    from carthage.personal_config import PersonalConfig
    monkeypatch.setattr(personal_image, 'runtime_ids', lambda: (0, 0))
    monkeypatch.setattr(personal_image.os, 'getuid', lambda: 1000)
    rendered = personal_image.render_personal_image_dockerfile('example/base:v2', PersonalConfig())
    assert 'usermod -o --uid 0 --gid 0 carthage' in rendered
    assert 'ENV HOME=/home/carthage' in rendered
    assert 'ENV IS_SANDBOX=1' in rendered


def test_startup_reuses_own_project_ports_but_rejects_other_owners(monkeypatch):
    from carthage import ports
    binding = HostPortBinding('dev', 5173, 5173, 'tcp', '127.0.0.1')
    monkeypatch.setattr(ports, 'carthage_owner_of_port', lambda port: 'demo')
    monkeypatch.setattr(ports, 'port_is_free', lambda *args: False)
    assert not ports.find_conflicts([binding], ignore_project='demo')
    assert ports.find_conflicts([binding], ignore_project='other')
    monkeypatch.setattr(ports, 'carthage_owner_of_port', lambda port: None)
    assert ports.find_conflicts([binding], ignore_project='demo')


def test_automatic_published_ports_can_be_hardened(monkeypatch):
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from carthage import ports
    cfg = SimpleNamespace(compose_file=Path('/tmp/compose.yaml'), compose_project_name='demo')
    document = {"services": {"dev": {"ports": [{"target": 3000, "protocol": "tcp"}]}}}
    monkeypatch.setattr(ports.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(document)))
    assert not ports.extract_host_ports(cfg)
    binding, = ports.extract_host_ports(cfg, include_automatic=True)
    assert binding.published is None
    assert binding.target == 3000
