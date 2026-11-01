from carthage import daemon


def test_rootless_ids_map_writable_bind_mounts_to_host_user(monkeypatch):
    monkeypatch.setattr(daemon, 'docker_security_options', lambda: ('name=rootless',))
    assert daemon.runtime_ids() == (0, 0)
    monkeypatch.setattr(daemon, 'docker_security_options', lambda: ('name=seccomp',))
    monkeypatch.setattr(daemon.os, 'getuid', lambda: 1234)
    monkeypatch.setattr(daemon.os, 'getgid', lambda: 5678)
    assert daemon.runtime_ids() == (1234, 5678)


def test_rootless_personal_image_preserves_home_and_unattended_agents(monkeypatch):
    from carthage import personal_image
    from carthage.personal_config import PersonalConfig
    monkeypatch.setattr(personal_image, 'runtime_ids', lambda: (0, 0))
    monkeypatch.setattr(personal_image.os, 'getuid', lambda: 1000)
    rendered = personal_image.render_personal_image_dockerfile('example/base:v2', PersonalConfig())
    assert 'usermod -o --uid 0 --gid 0 carthage' in rendered
    assert 'ENV HOME=/home/carthage' in rendered
    assert 'ENV IS_SANDBOX=1' in rendered
