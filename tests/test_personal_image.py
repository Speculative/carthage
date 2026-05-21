from carthage.personal_config import PersonalAgents, PersonalConfig, PersonalImage
from carthage.personal_image import (
    PERSONAL_IMAGE_HASH_LABEL,
    PersonalImageRequiredError,
    personal_image_is_current,
    personal_image_hash,
    personal_image_ref,
    read_personal_image_hash,
    render_personal_image_dockerfile,
    require_project_personal_image,
)


def test_personal_image_ref_defaults_to_expected_tag():
    assert personal_image_ref() == "carthage-base-personal:v1"


def test_personal_image_ref_can_be_overridden(monkeypatch):
    monkeypatch.setenv("CARTHAGE_PERSONAL_IMAGE_REPO", "carthage-base-personal-e2e-test")

    assert personal_image_ref() == "carthage-base-personal-e2e-test:v1"


def test_render_personal_image_without_packages():
    dockerfile = render_personal_image_dockerfile(
        "ghcr.io/speculative/carthage-base:v1",
        PersonalConfig(),
    )

    assert "FROM ghcr.io/speculative/carthage-base:v1" in dockerfile
    assert f"LABEL {PERSONAL_IMAGE_HASH_LABEL}=" in dockerfile
    assert "apt-get install" not in dockerfile
    assert dockerfile.endswith("WORKDIR /workspace\n")


def test_render_personal_image_with_apt_packages():
    dockerfile = render_personal_image_dockerfile(
        "ghcr.io/speculative/carthage-base:v1",
        PersonalConfig(image=PersonalImage(apt_packages=("fzf", "shellcheck"))),
    )

    assert "RUN apt-get update \\" in dockerfile
    assert "      fzf \\" in dockerfile
    assert "      shellcheck \\" in dockerfile
    assert "      shellcheck \\\n && rm -rf /var/lib/apt/lists/*" in dockerfile
    assert "rm -rf /var/lib/apt/lists/*" in dockerfile
    assert "USER carthage" in dockerfile


def test_render_personal_image_installs_codex_agent():
    dockerfile = render_personal_image_dockerfile(
        "ghcr.io/speculative/carthage-base:v1",
        PersonalConfig(agents=PersonalAgents(installed=("claude", "codex"), default="codex")),
    )

    assert "# Agent: codex" in dockerfile
    assert "# Agent: codex\nUSER carthage\nRUN npm config set prefix" in dockerfile
    assert "RUN npm config set prefix /home/carthage/.local" in dockerfile
    assert "RUN npm install -g @openai/codex" in dockerfile
    assert "RUN codex --version" in dockerfile
    assert "RUN codex --version\nUSER root" in dockerfile


def test_personal_image_hash_tracks_image_affecting_config():
    base = personal_image_hash(
        base_image="ghcr.io/speculative/carthage-base:v1",
        config=PersonalConfig(
            agents=PersonalAgents(installed=("codex",), default="codex"),
            image=PersonalImage(apt_packages=("fzf",)),
        ),
    )
    different_default = personal_image_hash(
        base_image="ghcr.io/speculative/carthage-base:v1",
        config=PersonalConfig(
            agents=PersonalAgents(installed=("codex",), default="claude"),
            image=PersonalImage(apt_packages=("fzf",)),
        ),
    )
    different_agent = personal_image_hash(
        base_image="ghcr.io/speculative/carthage-base:v1",
        config=PersonalConfig(
            agents=PersonalAgents(installed=("claude",), default="claude"),
            image=PersonalImage(apt_packages=("fzf",)),
        ),
    )
    different_package = personal_image_hash(
        base_image="ghcr.io/speculative/carthage-base:v1",
        config=PersonalConfig(
            agents=PersonalAgents(installed=("codex",), default="codex"),
            image=PersonalImage(apt_packages=("ripgrep",)),
        ),
    )

    assert different_default == base
    assert different_agent != base
    assert different_package != base


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


def test_require_project_personal_image_accepts_expected_from(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM carthage-base-personal:v1\n")

    assert require_project_personal_image(dockerfile, "v1") == "carthage-base-personal:v1"


def test_require_project_personal_image_rejects_published_base(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM ghcr.io/speculative/carthage-base:v1\n")

    try:
        require_project_personal_image(dockerfile, "v1")
    except PersonalImageRequiredError as exc:
        assert "carthage-base-personal:v1" in str(exc)
        assert "/carthage-annex --upgrade" in str(exc)
    else:
        raise AssertionError("expected PersonalImageRequiredError")
