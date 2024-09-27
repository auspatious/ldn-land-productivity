FROM ghcr.io/osgeo/gdal:ubuntu-small-3.6.3

# Don't use old pygeos
ENV USE_PYGEOS=0

RUN apt-get update && apt-get install -y \
    python3-dev \
    git \
    curl \
    ca-certificates \
    build-essential \
    && apt-get autoclean \
    && apt-get autoremove \
    && rm -rf /var/lib/{apt,dpkg,cache,log}

# Download the latest installer
ADD https://astral.sh/uv/install.sh /uv-installer.sh

# Run the installer then remove it
RUN sh /uv-installer.sh && rm /uv-installer.sh

# Ensure the installed binary is on the `PATH`
ENV PATH="/root/.cargo/bin/:$PATH"

# Copy from the cache instead of linking since it's a mounted volume
ENV UV_LINK_MODE=copy

WORKDIR /code
ADD . /code/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev && uv pip install hatch hatch-vcs

# Place executables in the environment at the front of the path
ENV PATH="/code/.venv/bin:$PATH"

# Smoketest
RUN ldn-processor --help
