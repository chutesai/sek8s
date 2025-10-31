FROM python:3.12 AS base

ENV \
  PYTHONUNBUFFERED=1 \
  PYTHONDONTWRITEBYTECODE=1 \
  POETRY_VERSION=2.1.2 \
  POETRY_VIRTUALENVS_IN_PROJECT=true \
  POETRY_NO_INTERACTION=1 \
  POETRY_HOME="/opt/poetry" \
  POETRY="/opt/poetry/bin/poetry"

ENV PATH="$POETRY_HOME/bin:$PATH"

ARG PROJECT_DIR

USER root

FROM base AS build
# Install poetry
RUN curl -sSL https://install.python-poetry.org | python3 - \
    && chmod a+x /opt/poetry/bin/poetry

WORKDIR /app
COPY pyproject.toml poetry.lock README.md ./
COPY ${PROJECT_DIR} ${PROJECT_DIR}/
RUN poetry install --only main

##################################################
## PRODUCTION ##
##################################################

FROM base AS production
ARG PROJECT_DIR

USER root

ENV PATH="/app/.venv/bin:$PATH"
ENV APP_USER=chutes
ENV APP_GROUP=chutes
RUN useradd ${APP_USER} -s /bin/bash -d /home/${APP_USER} && mkdir -p /home/${APP_USER} && chown ${APP_USER}:${APP_GROUP} /home/${APP_USER}

WORKDIR /app
COPY --from=build /app .

USER ${APP_USER}

##################################################
## DEVELOPMENT ##
##################################################

FROM build AS development

ENV PATH="/app/.venv/bin:$PATH"
ENV APP_USER=chutes
ENV APP_GROUP=chutes
ENV ENVIRONMENT=development
ENV DEBUG=true

RUN useradd ${APP_USER} -s /bin/bash -d /home/${APP_USER} && mkdir -p /home/${APP_USER} && chown ${APP_USER}:${APP_GROUP} /home/${APP_USER}

USER root

WORKDIR /app
COPY --chown=${APP_USER}:${APP_GROUP} ./${PROJECT_DIR} ./
RUN poetry install
RUN chown ${APP_USER}:${APP_GROUP} .

USER ${APP_USER}