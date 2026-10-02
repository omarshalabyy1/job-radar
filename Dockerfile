# One image for Airflow and the tracker. job_radar's packages live in their own virtual environment
# at /opt/job-radar-venv (the official image docs' pattern for keeping task dependencies apart from
# Airflow's), with Playwright's Chromium at /opt/ms-playwright for the career pages that build
# their job list in the browser. The repo itself is not copied in; docker-compose.yml mounts it at
# /opt/job-radar.

FROM apache/airflow:3.3.2-python3.10

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

USER root
RUN mkdir /opt/job-radar-venv /opt/ms-playwright && chown airflow:0 /opt/job-radar-venv /opt/ms-playwright

USER airflow
COPY requirements.txt /tmp/job-radar-requirements.txt
RUN python -m venv /opt/job-radar-venv \
    && /opt/job-radar-venv/bin/pip install --no-cache-dir -r /tmp/job-radar-requirements.txt \
    && /opt/job-radar-venv/bin/playwright install chromium

USER root
RUN /opt/job-radar-venv/bin/playwright install-deps chromium && rm -rf /var/lib/apt/lists/*
USER airflow
