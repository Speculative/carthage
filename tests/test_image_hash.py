"""Unit tests for image.parse_copied_sources — the tricky bit of image.py."""

from carthage.image import parse_base_image, parse_copied_sources


def test_basic_copy():
    assert parse_copied_sources("COPY app.py /workspace/") == ["app.py"]


def test_multiple_sources():
    assert parse_copied_sources("COPY a.py b.py /dst/") == ["a.py", "b.py"]


def test_copy_with_chown_flag():
    assert parse_copied_sources("COPY --chown=1000:1000 src/ /dst/") == ["src/"]


def test_copy_from_stage_is_skipped():
    # --from=<stage> references a prior build stage, not the local context.
    src = "COPY --from=builder /out/bin /usr/local/bin/bin"
    assert parse_copied_sources(src) == []


def test_add_with_http_source_skipped():
    assert parse_copied_sources("ADD https://example.com/x.tar.gz /tmp/") == []


def test_line_continuation():
    src = "COPY \\\n    a.py \\\n    b.py \\\n    /dst/\n"
    assert parse_copied_sources(src) == ["a.py", "b.py"]


def test_non_copy_lines_ignored():
    src = """FROM ubuntu:24.04
RUN echo hi
COPY app.py /workspace/
ENV X=1
"""
    assert parse_copied_sources(src) == ["app.py"]


def test_case_insensitive():
    assert parse_copied_sources("copy app.py /dst/") == ["app.py"]


def test_parse_base_image():
    assert parse_base_image("FROM carthage-base-personal:v1\n") == "carthage-base-personal:v1"


def test_parse_base_image_is_case_insensitive():
    assert parse_base_image("from ubuntu:24.04\n") == "ubuntu:24.04"


def test_parse_base_image_missing():
    assert parse_base_image("RUN echo hi\n") is None
