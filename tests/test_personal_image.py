from carthage.personal_config import PersonalConfig, PersonalImage
from carthage.personal_image import personal_image_ref, render_personal_image_dockerfile


def test_personal_image_ref_defaults_to_expected_tag():
    assert personal_image_ref() == "carthage-base-personal:v1"


def test_render_personal_image_without_packages():
    dockerfile = render_personal_image_dockerfile(
        "ghcr.io/speculative/carthage-base:v1",
        PersonalConfig(),
    )

    assert "FROM ghcr.io/speculative/carthage-base:v1" in dockerfile
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
