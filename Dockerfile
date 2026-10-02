# One image for Airflow and the tracker. job_radar's packages live in their own virtual environment
# at /opt/job-radar-venv (the official image docs' pattern for keeping task dependencies apart from
# Airflow's). The repo itself is not copied in; docker-compose.yml mounts it at /opt/job-radar.

FROM apache/airflow:3.3.2-python3.10

USER root
RUN mkdir /opt/job-radar-venv && chown airflow:0 /opt/job-radar-venv

USER airflow
COPY requirements.txt /tmp/job-radar-requirements.txt
RUN python -m venv /opt/job-radar-venv \
    && /opt/job-radar-venv/bin/pip install --no-cache-dir -r /tmp/job-radar-requirements.txt
