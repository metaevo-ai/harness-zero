FROM python:3.13-slim-bookworm AS dependencies
ENV DEBIAN_FRONTEND=noninteractive PIP_PROGRESS_BAR=off PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir appworld==0.1.3.post1 && appworld install

FROM dependencies AS client
# AHD setup checks this venv before trying an online install. The runtime
# network remains isolated; dependency installation belongs to image build.
RUN python -m venv /opt/ahd/venv && /opt/ahd/venv/bin/pip install --no-cache-dir openai==2.54.0
COPY runtime/client.py /opt/appworld_env/client.py
RUN printf '#!/bin/sh\nexec python /opt/appworld_env/client.py "$@"\n' > /usr/local/bin/appworld \
    && chmod +x /usr/local/bin/appworld && mkdir -p /workdir /logs
WORKDIR /workdir
CMD ["appworld", "serve"]

FROM dependencies AS world
ENV APPWORLD_ROOT=/opt/appworld
# The build context contains only explicitly selected public docs and input DBs.
COPY public_data/ /opt/appworld/data/
COPY runtime/server.py runtime/snapshot.py /opt/appworld_env/
RUN mkdir -p /export /opt/appworld/experiments
WORKDIR /opt/appworld
CMD ["python", "/opt/appworld_env/server.py"]

FROM world AS verifier
COPY runtime/grade.py /opt/appworld_env/grade.py
CMD ["sleep", "infinity"]
