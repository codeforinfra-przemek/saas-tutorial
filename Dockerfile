ARG PYTHON_VERSION=3.12-slim-bullseye
FROM python:${PYTHON_VERSION}

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH=/opt/venv/bin:$PATH

RUN python -m venv /opt/venv
RUN pip install --upgrade pip

RUN apt-get update && apt-get install -y \
    libpq-dev \
    libjpeg-dev \
    libcairo2 \
    gcc \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /code
WORKDIR /code

COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

COPY ./src/saashome /code

ARG PROJ_NAME="saashome"

RUN printf "#!/bin/bash\n" > ./railway_runner.sh && \
    printf "set -e\n\n" >> ./railway_runner.sh && \
    printf "RUN_PORT=\"\${PORT:-8000}\"\n\n" >> ./railway_runner.sh && \
    printf "python manage.py migrate --no-input\n" >> ./railway_runner.sh && \
    printf "gunicorn ${PROJ_NAME}.wsgi:application --bind \"[::]:\$RUN_PORT\"\n" >> ./railway_runner.sh

RUN chmod +x ./railway_runner.sh

RUN apt-get remove --purge -y gcc \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

CMD ./railway_runner.sh
