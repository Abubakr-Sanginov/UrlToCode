FROM python:3.12.3-slim-bookworm

ENV POETRY_VERSION 1.8.0

# Install system dependencies
RUN pip install "poetry==$POETRY_VERSION"

# Set work directory
WORKDIR /app

# Copy only requirements to cache them in docker layer
COPY poetry.lock pyproject.toml /app/

# Disable the creation of virtual environments
RUN poetry config virtualenvs.create false

# Install dependencies
RUN poetry install

# Install Chromium and Linux libraries needed by Playwright screenshot previews.
RUN playwright install --with-deps chromium

# The crawler drives a visible browser (headless Chromium gets served bot
# checks instead of pages), so the image needs a virtual display for it.
# xauth is a separate package on Debian and xvfb-run shells out to it: with
# only xvfb installed the container builds cleanly and then dies on every
# start with "xauth command not found" and exit status 3.
RUN apt-get update && apt-get install -y --no-install-recommends xvfb xauth \
    && rm -rf /var/lib/apt/lists/*

# Copy the current directory contents into the container at /app
COPY ./ /app/

RUN chmod +x /app/start.sh

# start.sh runs Xvfb and the application. It is not `xvfb-run`, which hangs
# here: that wrapper waits for a SIGUSR1 that never reaches it as PID 1, so
# uvicorn never starts and the platform finds no open port.
ENTRYPOINT ["/app/start.sh"]
# Shell form so ${PORT} expands. Hosts such as Render and Railway pick the
# port per deployment and health-check that one; a hard-coded 7001 answers a
# health check nobody is listening to.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-7001}
