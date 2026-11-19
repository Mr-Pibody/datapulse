"""Tests d'infrastructure et de déploiement pour DataPulse V14."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_dockerfile_multistage_and_security_hardening() -> None:
    dockerfile = Path("Dockerfile")
    assert dockerfile.exists()
    content = dockerfile.read_text(encoding="utf-8")

    assert "AS builder" in content
    assert "AS runtime" in content
    assert "USER datapulse" in content
    assert "EXPOSE 8000" in content
    assert "HEALTHCHECK" in content
    assert "/health/live" in content


def test_dockerignore_excludes_secrets_and_local_artifacts() -> None:
    dockerignore = Path(".dockerignore")
    assert dockerignore.exists()
    lines = {line.strip() for line in dockerignore.read_text(encoding="utf-8").splitlines()}

    assert ".git" in lines
    assert ".venv" in lines
    assert "*.sqlite" in lines
    assert ".env" in lines


def test_docker_compose_defines_api_and_watcher_services() -> None:
    compose_path = Path("docker-compose.yml")
    assert compose_path.exists()
    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    services = data.get("services", {})
    assert "datapulse-api" in services
    assert "datapulse-watcher" in services

    api_svc = services["datapulse-api"]
    assert "8000:8000" in api_svc["ports"]
    assert "healthcheck" in api_svc

    watcher_svc = services["datapulse-watcher"]
    assert "datapulse" in watcher_svc["command"]
    assert "watch" in watcher_svc["command"]


def test_github_actions_ci_workflow_is_valid_and_complete() -> None:
    ci_path = Path(".github/workflows/ci.yml")
    assert ci_path.exists()
    workflow = yaml.safe_load(ci_path.read_text(encoding="utf-8"))

    jobs = workflow.get("jobs", {})
    assert "quality-and-tests" in jobs
    assert "docker-build" in jobs

    steps_text = "\n".join(
        step.get("run", "") for step in jobs["quality-and-tests"].get("steps", [])
    )
    assert "ruff format --check src tests" in steps_text
    assert "ruff check src tests" in steps_text
    assert "mypy src" in steps_text
    assert "pytest --cov=datapulse" in steps_text

    readme = Path("README.md").read_text(encoding="utf-8")
    assert "DataPulse v14.0.0" in readme
    assert "docker compose up -d --build" in readme
