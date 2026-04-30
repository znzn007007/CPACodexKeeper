# Repository Guidelines

## Project Structure & Module Organization
CPACodexKeeper is a small Python 3.11+ service for maintaining `type=codex` tokens from a CPA management API. Runtime code lives in `src/`: `cli.py` handles command-line entry, `settings.py` reads environment configuration, `maintainer.py` contains the maintenance loop, and client/notifier helpers are split into focused modules. `main.py` is the thin executable entry point. Tests live in `tests/` and mirror the main areas (`test_cli.py`, `test_settings.py`, `test_maintainer.py`). Deployment files are at the repository root: `Dockerfile`, `docker-compose.yml`, `.env.example`, and `justfile`. `docs/solutions/` stores documented solutions and best practices, organized by category with YAML frontmatter such as `module`, `tags`, and `problem_type`; it is relevant when implementing, debugging, or making workflow decisions in already-documented areas.

## Build, Test, and Development Commands
- `python -m pip install -r requirements.txt` or `just install`: install runtime dependencies.
- `python -m unittest discover -s tests` or `just test`: run the full unit test suite used by CI.
- `python main.py --once --dry-run` or `just dry-run`: execute one maintenance pass without mutating remote state.
- `python main.py --once` / `just run-once`: execute one real pass.
- `python main.py` / `just daemon`: run continuously.
- `docker build -t cpacodexkeeper .` or `just docker-build`: validate the container image.

## Coding Style & Naming Conventions
Use standard Python style with 4-space indentation, type hints for public functions and dataclasses, and clear module-level constants in `UPPER_SNAKE_CASE`. Prefer small functions with explicit names such as `load_settings`, `run_forever`, or `_parse_bool`. Keep private helpers prefixed with `_`. Do not add new dependencies unless necessary; reuse the existing standard library plus `curl-cffi`.

## Testing Guidelines
The project uses `unittest`. Name files `tests/test_<area>.py` and test methods `test_<expected_behavior>`. Add or update tests for settings parsing, CLI behavior, token state transitions, and Docker compose changes. Use mocks for network or remote CPA/OpenAI calls; tests should not require real credentials.

## Commit & Pull Request Guidelines
Recent history follows Conventional Commits, often with scopes: `fix(refresh): ...`, `fix(quota): ...`, `feat(settings): ...`. Keep the subject imperative and concise; Chinese or English is acceptable if it matches the change context. Pull requests should include a short problem summary, the chosen fix, test results (`python -m unittest discover -s tests`), and any configuration or Docker impact. Link related issues and include logs/screenshots only when behavior is user-visible.

## Security & Configuration Tips
Never commit `.env`, CPA tokens, OpenAI tokens, Feishu secrets, or runtime state. Start from `.env.example`, keep local values private, and prefer `--dry-run` before enabling daemon or Docker operation.
