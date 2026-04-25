FROM carthage-base:e2e
# redis-cli for the in-container redis ping test.
USER root
RUN apt-get update && apt-get install -y --no-install-recommends redis-tools \
 && rm -rf /var/lib/apt/lists/*
USER carthage
