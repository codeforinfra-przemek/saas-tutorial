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

COPY requirements.txt /tmp/app-requirements.txt
COPY datacollector/requirements.txt /tmp/datacollector-requirements.txt
RUN pip install \
    -r /tmp/app-requirements.txt \
    -r /tmp/datacollector-requirements.txt

COPY ./datacollector /code/datacollector
COPY ./src/saashome /code/src/saashome

WORKDIR /code/src/saashome

COPY ./railway_runner.sh /usr/local/bin/railway_runner.sh

RUN chmod +x /usr/local/bin/railway_runner.sh

# Fail the image build when Django cannot import the deployed application.
RUN DJANGO_SECRET_KEY=build-only-not-used-at-runtime python manage.py check

RUN apt-get remove --purge -y gcc \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

CMD ["/usr/local/bin/railway_runner.sh"]
