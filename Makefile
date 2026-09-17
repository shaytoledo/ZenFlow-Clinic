# ZenFlow Clinic — developer targets (Phase 0.2). Windows without `make`: python tasks.py <target>
PY ?= python
SRC = bot web startup zenflow tests

.PHONY: install fmt lint type test test-fast security all lock hooks

install:        ## install runtime + dev deps into the active venv
	$(PY) -m pip install -r requirements.txt -r requirements-dev.txt

fmt:            ## format code (black, then ruff import sorting / autofixes)
	$(PY) -m black $(SRC)
	$(PY) -m ruff check --fix $(SRC)

lint:           ## check formatting + lint without changing files
	$(PY) -m black --check $(SRC)
	$(PY) -m ruff check $(SRC)

type:           ## mypy (permissive baseline; strict for bot/interfaces + web/repositories)
	$(PY) -m mypy

test:           ## full suite with coverage gate
	$(PY) -m pytest --cov --cov-report=term-missing

test-fast:      ## quick feedback loop
	$(PY) -m pytest -m "not slow" -x -q

security:       ## static security scan + attack-scenario tests
	$(PY) -m bandit -q -r bot web startup -x tests
	$(PY) -m pytest tests/security -q

all: lint type test security   ## the local gate

lock:           ## regenerate pinned lockfiles from requirements*.in
	$(PY) -m piptools compile --strip-extras --no-header -o requirements.txt requirements.in
	$(PY) -m piptools compile --strip-extras --no-header -o requirements-dev.txt requirements-dev.in

hooks:          ## install git hooks
	$(PY) -m pre_commit install
