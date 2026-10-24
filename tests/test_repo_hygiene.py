"""
Invariant I9 (no secrets or generated artifacts in git or images) and the CI contract.
These are file-level checks: they fail if someone deletes the ignore files or the workflow.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _lines(path: Path):
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.strip().startswith("#")]


def test_gitignore_keeps_secrets_databases_and_old_model_artifacts_out_of_git():
    lines = _lines(ROOT / ".gitignore")
    for needed in (".env", "*.db", "node_modules/", "models/*_v*.joblib"):
        assert needed in lines, f".gitignore must contain {needed}"
    assert "!.env.example" in lines


def test_the_python_image_cannot_contain_secrets_databases_or_the_git_history():
    path = ROOT / ".dockerignore"
    assert path.exists(), "no .dockerignore: `COPY . .` would bake .env, the database and .git into the image"
    lines = _lines(path)
    for needed in (".git", ".env", ".env.*", "!.env.example", "*.db", "node_modules", "dashboard/node_modules",
                   "dashboard/dist", "proj", "models/*_v[2-9]*", "__pycache__"):
        assert needed in lines, f".dockerignore must contain {needed}"


def test_the_python_image_still_gets_what_it_needs_to_run():
    """Ignoring too much would break the image: the baseline model and the data files must not be excluded."""
    lines = _lines(ROOT / ".dockerignore")
    for must_not in ("models", "models/", "data", "data/", "*.csv", "*.joblib", "serving", "requirements.txt"):
        assert must_not not in lines, f".dockerignore must not exclude {must_not}"
    assert (ROOT / "models" / "model_v1.joblib").exists() and (ROOT / "data" / "training_data.csv").exists()


def test_the_dashboard_image_build_context_is_clean():
    path = ROOT / "dashboard" / ".dockerignore"
    assert path.exists()
    lines = _lines(path)
    for needed in ("node_modules", "dist", ".env", ".env.*"):
        assert needed in lines, f"dashboard/.dockerignore must contain {needed}"


def test_ci_runs_the_tests_lint_build_and_the_docker_builds():
    workflow = ROOT / ".github" / "workflows" / "ci.yml"
    assert workflow.exists(), "no CI workflow"
    text = workflow.read_text(encoding="utf-8")
    for needed in ("pytest", "requirements-dev.txt", "npm ci", "npm run lint", "npm run build",
                   "docker build", "3.11", "3.12", "pull_request"):
        assert needed in text, f"ci.yml should mention {needed}"
