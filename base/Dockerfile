# syntax=docker/dockerfile:1.7
#
# carthage-base — the foundation image for every Carthage project.
#
# What lives here:
#   - git, tmux, curl, openssh-client, jq, ripgrep, ca-certificates
#   - Python 3.12 + uv
#   - Node.js LTS (required by Claude Code)
#   - Claude Code CLI
#   - /etc/carthage/SANDBOX.md (sandbox-awareness document)
#   - a non-root `carthage` user whose UID/GID match the host
#
# What does NOT live here:
#   - Project-specific runtimes/toolchains (Rust, Go, pyenv versions, nvm, etc.) —
#     those belong in each project's `.carthage/Dockerfile` which FROMs this image.
#   - docker/podman CLI or the docker socket — the container never needs to run containers.
#   - passwordless sudo — system packages are Dockerfile-time, not runtime.
#   - An in-container host-LAN firewall. Carthage does NOT isolate the container
#     from the host's LAN; see README "Security model" for what we do and do not
#     defend against.

FROM ubuntu:24.04

ARG HOST_UID=1000
ARG HOST_GID=1000
ARG NODE_MAJOR=20

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    CARTHAGE=1 \
    PATH=/home/carthage/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    PAGER=less \
    EDITOR=vim \
    VISUAL=vim \
    CLAUDE_CODE_NO_FLICKER=1

# --- System packages --------------------------------------------------------
# passwd gives us groupadd/useradd. libcap2-bin gives us `capsh` which the
# hardening acceptance check uses. We intentionally do NOT install sudo.
# Ubuntu's minbase image strips /usr/share/doc/*, but fzf ships its bash
# key-binding script there — keep fzf's docs so we can source key-bindings.bash.
RUN echo 'path-include=/usr/share/doc/fzf/*' > /etc/dpkg/dpkg.cfg.d/keep-fzf-docs \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      bat \
      ca-certificates \
      curl \
      fzf \
      git \
      git-delta \
      gnupg \
      jq \
      less \
      libcap2-bin \
      openssh-client \
      passwd \
      python3.12 \
      python3.12-venv \
      python3-pip \
      ripgrep \
      tmux \
      tzdata \
      vim \
 && rm -rf /var/lib/apt/lists/*

# --- CLI tweaks ------------------------------------------------------------
# Ubuntu ships `bat` as `batcat` to avoid a binary-name conflict; symlink so
# `bat` works directly. System-level git config sets delta as the diff pager;
# any host-mounted ~/.gitconfig with its own core.pager still wins. Wire fzf
# Ctrl-R / Ctrl-T / Alt-C bindings into the system bashrc so every interactive
# bash shell picks them up.
RUN ln -s /usr/bin/batcat /usr/local/bin/bat \
 && git config --system core.pager delta \
 && git config --system interactive.diffFilter 'delta --color-only' \
 && git config --system delta.navigate true \
 && printf '\n# fzf key bindings (Ctrl-R history, Ctrl-T files, Alt-C cd)\n[ -f /usr/share/doc/fzf/examples/key-bindings.bash ] && source /usr/share/doc/fzf/examples/key-bindings.bash\n' >> /etc/bash.bashrc

# --- Node.js LTS (for Claude Code) -----------------------------------------
RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/* \
 && node --version \
 && npm --version

# --- Claude Code CLI -------------------------------------------------------
RUN npm install -g @anthropic-ai/claude-code \
 && claude --version

# --- uv (system-wide; users get it on PATH via /usr/local/bin) -------------
RUN curl -LsSf https://astral.sh/uv/install.sh | \
      env UV_INSTALL_DIR=/usr/local/bin sh \
 && uv --version

# --- Non-root user matching host UID/GID -----------------------------------
# On Ubuntu 24.04 the default `ubuntu` user already claims UID 1000. If the
# host's UID/GID is 1000 we rename/repurpose it; otherwise we create fresh.
RUN set -eux; \
    if getent passwd "${HOST_UID}" >/dev/null; then \
        existing_user=$(getent passwd "${HOST_UID}" | cut -d: -f1); \
        if [ "${existing_user}" != "carthage" ]; then \
            userdel -r "${existing_user}" 2>/dev/null || true; \
        fi; \
    fi; \
    if getent group "${HOST_GID}" >/dev/null; then \
        existing_group=$(getent group "${HOST_GID}" | cut -d: -f1); \
        if [ "${existing_group}" != "carthage" ]; then \
            groupdel "${existing_group}" 2>/dev/null || true; \
        fi; \
    fi; \
    groupadd --gid "${HOST_GID}" carthage; \
    useradd --uid "${HOST_UID}" --gid "${HOST_GID}" \
            --shell /bin/bash --create-home carthage

# --- Sandbox-awareness doc -------------------------------------------------
RUN mkdir -p /etc/carthage
COPY SANDBOX.md /etc/carthage/SANDBOX.md
COPY entrypoint.sh /usr/local/bin/carthage-entrypoint
RUN chmod 0755 /usr/local/bin/carthage-entrypoint \
 && chmod 0644 /etc/carthage/SANDBOX.md

# --- tmux config -----------------------------------------------------------
# Status bar reads $CARTHAGE_PROJECT (set by compose); falls back to a
# placeholder so a bare `docker run` still renders.
COPY --chown=carthage:carthage tmux.conf /home/carthage/.tmux.conf
RUN chmod 0644 /home/carthage/.tmux.conf

WORKDIR /workspace
USER carthage

ENTRYPOINT ["/usr/local/bin/carthage-entrypoint"]
CMD ["sleep", "infinity"]
