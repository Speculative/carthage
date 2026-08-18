from pathlib import Path

from carthage.personal_config import PersonalAgents, PersonalConfig, PersonalImage, PersonalImageFile
from carthage.personal_image import (
    PERSONAL_IMAGE_HASH_LABEL,
    PersonalImageRequiredError,
    build_personal_image,
    personal_image_is_current,
    personal_image_hash,
    personal_image_ref,
    read_personal_image_hash,
    render_personal_image_dockerfile,
    require_project_personal_image,
)


def test_personal_image_ref_defaults_to_expected_tag():
    assert personal_image_ref() == "carthage-base-personal:v2"


def test_personal_image_ref_can_be_overridden(monkeypatch):
    monkeypatch.setenv("CARTHAGE_PERSONAL_IMAGE_REPO", "carthage-base-personal-e2e-test")

    assert personal_image_ref() == "carthage-base-personal-e2e-test:v2"


def test_render_personal_image_without_packages():
    dockerfile = render_personal_image_dockerfile(
        "ghcr.io/speculative/carthage-base:v2",
        PersonalConfig(),
    )

    assert "FROM ghcr.io/speculative/carthage-base:v2" in dockerfile
    assert f"LABEL {PERSONAL_IMAGE_HASH_LABEL}=" in dockerfile
    assert "apt-get install" not in dockerfile
    assert dockerfile.endswith("WORKDIR /workspace\n")


def test_render_personal_image_with_apt_packages():
    dockerfile = render_personal_image_dockerfile(
        "ghcr.io/speculative/carthage-base:v2",
        PersonalConfig(image=PersonalImage(apt_packages=("fzf", "shellcheck"))),
    )

    assert "RUN apt-get update \\" in dockerfile
    assert "      fzf \\" in dockerfile
    assert "      shellcheck \\" in dockerfile
    assert "      shellcheck \\\n && rm -rf /var/lib/apt/lists/*" in dockerfile
    assert "rm -rf /var/lib/apt/lists/*" in dockerfile
    assert "USER carthage" in dockerfile


def test_render_personal_image_compiles_terminfo_sources():
    dockerfile = render_personal_image_dockerfile(
        "ghcr.io/speculative/carthage-base:v2",
        PersonalConfig(image=PersonalImage(terminfo_files=("/tmp/xterm-ghostty",))),
    )

    assert "      ncurses-bin \\\n && rm -rf /var/lib/apt/lists/*" in dockerfile
    assert "COPY terminfo/ /tmp/carthage-terminfo/" in dockerfile
    assert 'tic -x -o /usr/share/terminfo "$source"' in dockerfile


def test_render_personal_image_copies_personal_files():
    dockerfile = render_personal_image_dockerfile(
        "ghcr.io/speculative/carthage-base:v2",
        PersonalConfig(
            image=PersonalImage(
                files=(PersonalImageFile("/home/jtao/go/bin/ostraka", "/usr/local/bin/ostraka", "0755"),),
            ),
        ),
    )

    assert 'COPY ["files/0", "/usr/local/bin/ostraka"]' in dockerfile
    assert "RUN chmod 0755 /usr/local/bin/ostraka" in dockerfile


def test_render_personal_image_installs_codex_agent():
    dockerfile = render_personal_image_dockerfile(
        "ghcr.io/speculative/carthage-base:v2",
        PersonalConfig(agents=PersonalAgents(installed=("claude", "codex"), default="codex")),
    )

    assert "# Agent: codex" in dockerfile
    assert "# Agent: codex\nRUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash -" in dockerfile
    assert "RUN apt-get install -y --no-install-recommends nodejs" in dockerfile
    assert "RUN npm --version\nUSER carthage\nRUN npm config set prefix" in dockerfile
    assert "RUN npm config set prefix /home/carthage/.local" in dockerfile
    assert "RUN npm install -g @openai/codex" in dockerfile
    assert "RUN codex --version" in dockerfile
    assert "RUN codex --version\nUSER root" in dockerfile


def test_render_personal_image_installs_claude_agent():
    dockerfile = render_personal_image_dockerfile(
        "ghcr.io/speculative/carthage-base:v2",
        PersonalConfig(agents=PersonalAgents(installed=("claude",), default="claude")),
    )

    assert "# Agent: claude" in dockerfile
    assert "# Agent: claude\nUSER carthage\nRUN curl -fsSL https://claude.ai/install.sh | bash" in dockerfile
    assert "RUN claude --version" in dockerfile
    assert "RUN claude --version\nUSER root" in dockerfile


def test_personal_image_hash_tracks_image_affecting_config():
    base = personal_image_hash(
        base_image="ghcr.io/speculative/carthage-base:v2",
        config=PersonalConfig(
            agents=PersonalAgents(installed=("codex",), default="codex"),
            image=PersonalImage(apt_packages=("fzf",)),
        ),
    )
    different_default = personal_image_hash(
        base_image="ghcr.io/speculative/carthage-base:v2",
        config=PersonalConfig(
            agents=PersonalAgents(installed=("codex",), default="claude"),
            image=PersonalImage(apt_packages=("fzf",)),
        ),
    )
    different_agent = personal_image_hash(
        base_image="ghcr.io/speculative/carthage-base:v2",
        config=PersonalConfig(
            agents=PersonalAgents(installed=("claude",), default="claude"),
            image=PersonalImage(apt_packages=("fzf",)),
        ),
    )
    different_package = personal_image_hash(
        base_image="ghcr.io/speculative/carthage-base:v2",
        config=PersonalConfig(
            agents=PersonalAgents(installed=("codex",), default="codex"),
            image=PersonalImage(apt_packages=("ripgrep",)),
        ),
    )

    assert different_default == base
    assert different_agent != base
    assert different_package != base


def test_personal_image_hash_tracks_terminfo_source_contents(tmp_path):
    source = tmp_path / "xterm-ghostty"
    source.write_text("xterm-ghostty|first,\n\tcols#80,\n")
    config = PersonalConfig(image=PersonalImage(terminfo_files=(str(source),)))

    first = personal_image_hash(base_image="base:v2", config=config)
    source.write_text("xterm-ghostty|second,\n\tcols#80,\n")
    second = personal_image_hash(base_image="base:v2", config=config)

    assert second != first


def test_personal_image_hash_tracks_copied_file_contents_and_destination(tmp_path):
    source = tmp_path / "ostraka"
    source.write_bytes(b"first")
    config = PersonalConfig(
        image=PersonalImage(files=(PersonalImageFile(str(source), "/usr/local/bin/ostraka", "0755"),)),
    )

    first = personal_image_hash(base_image="base:v2", config=config)
    source.write_bytes(b"second")
    second = personal_image_hash(base_image="base:v2", config=config)
    moved = personal_image_hash(
        base_image="base:v2",
        config=PersonalConfig(
            image=PersonalImage(files=(PersonalImageFile(str(source), "/usr/bin/ostraka", "0755"),)),
        ),
    )

    assert second != first
    assert moved != second


def test_personal_image_hash_tracks_root_agent_install_commands(monkeypatch):
    from carthage import agents
    from carthage.agents import AgentProfile

    base_profile = AgentProfile(
        kind="test",
        binary="test",
        root_install_commands=("install-a",),
        install_commands=("test --version",),
        launch_command="test",
        tmux_window="test",
    )
    changed_profile = AgentProfile(
        kind="test",
        binary="test",
        root_install_commands=("install-b",),
        install_commands=("test --version",),
        launch_command="test",
        tmux_window="test",
    )
    config = PersonalConfig(agents=PersonalAgents(installed=("test",), default="test"))

    monkeypatch.setitem(agents.AGENT_PROFILES, "test", base_profile)
    first = personal_image_hash(base_image="base:v2", config=config)

    monkeypatch.setitem(agents.AGENT_PROFILES, "test", changed_profile)
    second = personal_image_hash(base_image="base:v2", config=config)

    assert first != second


def test_personal_image_is_current_compares_stored_hash(monkeypatch):
    config = PersonalConfig(agents=PersonalAgents(installed=("codex",), default="codex"))
    expected = personal_image_hash(base_image="base:v1", config=config)
    monkeypatch.setattr(
        "carthage.personal_image.read_personal_image_hash",
        lambda image_ref: expected,
    )

    current, detail = personal_image_is_current(
        image_ref="personal:v1",
        base_image="base:v1",
        config=config,
    )

    assert current is True
    assert expected in detail


def test_personal_image_is_current_detects_stale_hash(monkeypatch):
    config = PersonalConfig(agents=PersonalAgents(installed=("codex",), default="codex"))
    monkeypatch.setattr(
        "carthage.personal_image.read_personal_image_hash",
        lambda image_ref: "old",
    )

    current, detail = personal_image_is_current(
        image_ref="personal:v1",
        base_image="base:v1",
        config=config,
    )

    assert current is False
    assert "stale" in detail


def test_read_personal_image_hash_handles_missing_image(monkeypatch):
    import subprocess

    def fake_run(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "docker")

    monkeypatch.setattr("carthage.personal_image.subprocess.run", fake_run)

    assert read_personal_image_hash("missing:v1") is None


def test_build_personal_image_uses_docker_cache_by_default(monkeypatch):
    import subprocess

    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("carthage.personal_image.subprocess.run", fake_run)

    ok, detail = build_personal_image(
        base_image="base:v2",
        target_image="personal:v2",
        config=PersonalConfig(),
    )

    assert ok is True
    assert detail == "personal:v2"
    args, kwargs = calls[0]
    assert args[:5] == ["docker", "build", "-t", "personal:v2", "-f"]
    assert "--no-cache" not in args
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


def test_build_personal_image_can_disable_docker_cache(monkeypatch):
    import subprocess

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("carthage.personal_image.subprocess.run", fake_run)

    ok, detail = build_personal_image(
        base_image="base:v2",
        target_image="personal:v2",
        config=PersonalConfig(),
        no_cache=True,
    )

    assert ok is True
    assert detail == "personal:v2"
    assert "--no-cache" in calls[0]


def test_build_personal_image_stages_terminfo_source(tmp_path, monkeypatch):
    import subprocess

    source = tmp_path / "xterm-ghostty"
    source.write_text("xterm-ghostty|Ghostty terminal,\n\tcols#80,\n")
    staged = {}

    def fake_run(args, **kwargs):
        context = args[-1]
        staged["source"] = (Path(context) / "terminfo" / "0").read_text()
        staged["dockerfile"] = (Path(context) / "Dockerfile").read_text()
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("carthage.personal_image.subprocess.run", fake_run)

    ok, detail = build_personal_image(
        base_image="base:v2",
        target_image="personal:v2",
        config=PersonalConfig(image=PersonalImage(terminfo_files=(str(source),))),
    )

    assert (ok, detail) == (True, "personal:v2")
    assert staged["source"] == source.read_text()
    assert "COPY terminfo/ /tmp/carthage-terminfo/" in staged["dockerfile"]


def test_build_personal_image_stages_personal_files(tmp_path, monkeypatch):
    import subprocess

    source = tmp_path / "ostraka"
    source.write_bytes(b"binary")
    staged = {}

    def fake_run(args, **kwargs):
        context = args[-1]
        staged["source"] = (Path(context) / "files" / "0").read_bytes()
        staged["dockerfile"] = (Path(context) / "Dockerfile").read_text()
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("carthage.personal_image.subprocess.run", fake_run)

    ok, detail = build_personal_image(
        base_image="base:v2",
        target_image="personal:v2",
        config=PersonalConfig(
            image=PersonalImage(files=(PersonalImageFile(str(source), "/usr/local/bin/ostraka", "0755"),)),
        ),
    )

    assert (ok, detail) == (True, "personal:v2")
    assert staged["source"] == source.read_bytes()
    assert 'COPY ["files/0", "/usr/local/bin/ostraka"]' in staged["dockerfile"]


def test_require_project_personal_image_accepts_expected_from(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM carthage-base-personal:v2\n")

    assert require_project_personal_image(dockerfile, "v2") == "carthage-base-personal:v2"


def test_require_project_personal_image_rejects_published_base(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM ghcr.io/speculative/carthage-base:v2\n")

    try:
        require_project_personal_image(dockerfile, "v2")
    except PersonalImageRequiredError as exc:
        assert "carthage-base-personal:v2" in str(exc)
        assert "/carthage-annex --upgrade" in str(exc)
    else:
        raise AssertionError("expected PersonalImageRequiredError")
