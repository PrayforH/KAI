"""Offline, credential-free project scaffolding for the DeepAgents exporter."""

from __future__ import annotations

import json

_ASSETS = '''"""Read packaged assets without writing into the installed package."""
from pathlib import Path
import shutil

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def load_prompt() -> str:
    return (PACKAGE_ROOT / "prompts" / "system.md").read_text(encoding="utf-8")


def materialize_skills(workspace: Path) -> None:
    source = PACKAGE_ROOT / "skills"
    if not source.is_dir():
        return
    target = workspace / "skills"
    target.mkdir(parents=True, exist_ok=True)
    # Idempotent imports avoid a langgraph dev reload loop. Preserve unrelated
    # user files, and never copy Python's generated caches into the workspace.
    for asset in source.rglob("*"):
        relative = asset.relative_to(source)
        if "__pycache__" in relative.parts or asset.suffix == ".pyc":
            continue
        destination = target / relative
        if asset.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif not destination.is_file() or destination.read_bytes() != asset.read_bytes():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(asset, destination)
'''

_DELEGATION = '''from langchain.agents.middleware import AgentMiddleware


class NoSubagentsMiddleware(AgentMiddleware):
    """Replace implicit general-purpose delegation with the exported bindings."""
    @property
    def name(self):
        return "SubAgentMiddleware"
'''

_RUN = '''"""Start the local LangGraph dev server from the exported project root."""
from pathlib import Path
import subprocess
import sys


def main() -> None:
    from sapling_deep_agents.config.settings import PROJECT_ROOT
    config = PROJECT_ROOT / "langgraph.json"
    if not config.is_file():
        raise SystemExit("Run from the exported project root or set DEEPAGENTS_PROJECT_ROOT.")
    name = "langgraph.exe" if sys.platform == "win32" else "langgraph"
    executable = Path(sys.executable).parent / name
    raise SystemExit(subprocess.call(
        [str(executable), "dev", "--config", str(config), *sys.argv[1:]], cwd=PROJECT_ROOT,
    ))


if __name__ == "__main__":
    main()
'''

_TEST = '''"""No provider credentials or network calls are needed for these contracts."""
import importlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_entry_point_and_packaged_assets():
    import sapling_deep_agents
    from sapling_deep_agents.controller.agent import agent
    from sapling_deep_agents.services.assets import load_prompt
    assert callable(agent)
    assert isinstance(load_prompt(), str)
    package = Path(sapling_deep_agents.__file__).parent
    for prompt in package.rglob("prompts/system.md"):
        assert isinstance(prompt.read_text(encoding="utf-8"), str)
    for operator in package.rglob("tools/operators/*.py"):
        if operator.name != "__init__.py":
            name = ".".join(operator.relative_to(package.parent).with_suffix("").parts)
            assert callable(importlib.import_module(name).run)


def test_langgraph_configuration():
    config = json.loads((ROOT / "langgraph.json").read_text())
    assert config["dependencies"] == ["."]
    assert set(config["graphs"].values()) == {"./agent.py:agent"}
'''


def render_settings(*, name: str, model: str, module_prefix: str, recursion_limit: int) -> str:
    # Preserve the pre-layout child model override names.
    suffix = module_prefix.replace("agents.subagents.", "subagents.")
    env = "DEEPAGENTS_MODEL" + ("_" + suffix.replace(".", "_").upper().strip("_") if suffix else "")
    workspace = "/".join(module_prefix.strip(".").split(".")) if module_prefix else ""
    return f'''"""Configuration for {name}; inherited environment wins over .env."""
import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(os.environ.get("DEEPAGENTS_PROJECT_ROOT", str(Path.cwd()))).resolve()
load_dotenv(PROJECT_ROOT / ".env")
AGENT_NAME = {name!r}
MODEL = os.environ.get({env!r}, {model!r})
WORKSPACE_ROOT = Path(os.environ.get(
    "DEEPAGENTS_WORKSPACE", str(PROJECT_ROOT / "workspace")),
).resolve()
WORKSPACE_ROOT = WORKSPACE_ROOT / {workspace!r}
RECURSION_LIMIT = {recursion_limit}
'''


def project_scaffold(namespace: str) -> dict[str, str]:
    """Return shared package files and root-only tooling (no dependency resolution)."""
    root = "src/sapling_deep_agents/"
    files = {
        root + "__init__.py": '"""Standalone DeepAgents project exported by Agent Studio."""\n',
        root + "services/assets.py": _ASSETS,
        root + "middleware/delegation.py": _DELEGATION,
        root + "controller/agent.py": (
            f"from {namespace}agents.agent import agent, build_agent\n\n"
            '__all__ = ["agent", "build_agent"]\n'
        ),
        root + "run/app.py": _RUN,
        "agent.py": '"""Compatibility entry; edit src/sapling_deep_agents/agents/agent.py."""\n'
        "from sapling_deep_agents.controller.agent import agent, build_agent\n\n"
        '__all__ = ["agent", "build_agent"]\n',
        "test/test_project.py": _TEST,
        "MANIFEST.in": (
            "include agent.py langgraph.json agent-studio.json .env.example\n"
            "include .gitignore .dockerignore .gitlab-ci.yml .pre-commit-config.yaml\n"
            "include pyrightconfig.json AGENTS.md\n"
            "graft src/sapling_deep_agents\ngraft docker\ngraft test\n"
            "global-exclude __pycache__ *.py[cod]\n"
        ),
        "docker/Dockerfile": """FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md MANIFEST.in ./
COPY src ./src
RUN python -m pip install --no-cache-dir . && useradd --create-home agent
COPY agent.py langgraph.json ./
RUN mkdir -p workspace .langgraph_api && chown -R agent:agent /app
USER agent
EXPOSE 2024
CMD ["langgraph", "dev", "--host", "0.0.0.0", "--port", "2024", "--no-browser"]
""",
        "docker/compose.yaml": """services:
  agent:
    build:
      context: ..
      dockerfile: docker/Dockerfile
    ports:
      - "127.0.0.1:2024:2024"
    env_file:
      - ../.env
    volumes:
      - ../.env:/app/.env:ro
      - workspace:/app/workspace
volumes:
  workspace:
""",
        ".dockerignore": (
            ".git\n.env\n.env.*\n.venv\n**/__pycache__\nworkspace\n"
            ".langgraph_api\ndist\nbuild\n*.egg-info\n"
        ),
        ".gitlab-ci.yml": """image: python:3.12-slim
stages: [test]
project-check:
  stage: test
  script:
    - python -m pip install -e '.[dev]'
    - python -m ruff check src test
    - python -m pytest
    - python -m build
  artifacts:
    paths: [dist/]
""",
        ".pre-commit-config.yaml": """repos:
  - repo: local
    hooks:
      - id: python-syntax
        name: Check Python syntax
        entry: python -m compileall -q src test
        language: system
        pass_filenames: false
        types: [python]
""",
        "pyrightconfig.json": json.dumps(
            {"include": ["src"], "pythonVersion": "3.12", "typeCheckingMode": "basic"}, indent=2
        )
        + "\n",
        "AGENTS.md": """# Exported project maintenance

Install with `python -m pip install -e '.[dev]'`, then run `python -m pytest`.
The LangGraph factory must stay synchronous; LangGraph manages its checkpointer.
Edit prompts under `src/sapling_deep_agents/prompts/` and operators under `tools/operators/`.
Keep credentials in `.env` or the process environment. Preserve pinned subagent assets.
Run `uv lock` after dependency changes; commit the resulting lock for your own package index.
""",
    }
    for directory in (
        "agents",
        "config",
        "controller",
        "middleware",
        "models",
        "prompts",
        "run",
        "services",
        "skills",
        "tools",
        "utils",
    ):
        # tools/__init__.py is written by the operator exporter.
        if directory != "tools":
            files[root + directory + "/__init__.py"] = (
                f'"""{directory.capitalize()} for the exported agent."""\n'
            )
    return files
