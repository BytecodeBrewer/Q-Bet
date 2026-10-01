from pathlib import Path
import tomllib


ROOT = Path(__file__).parents[2]
WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
    encoding="utf-8"
)
PREVIEW_MIGRATIONS = (ROOT / ".github" / "workflows" / "preview-migrations.yml").read_text(
    encoding="utf-8"
)


def _job(name: str, next_name: str | None = None) -> str:
    start = WORKFLOW.index(f"  {name}:\n")
    end = WORKFLOW.index(f"  {next_name}:\n", start) if next_name else len(WORKFLOW)
    return WORKFLOW[start:end]


def test_pull_request_vercel_gate_builds_and_exercises_protected_candidate() -> None:
    block = _job("vercel-build-check", "deploy")

    assert "      - develop\n      - main" in WORKFLOW
    assert "!cancelled()" in block
    assert "needs.validation.result == 'success'" in block
    assert "needs.performance.result == 'success'" in block
    assert "github.event_name == 'pull_request'" in block
    assert "github.event.pull_request.head.sha" in block
    assert "refs/heads/develop" in block
    assert "refs/heads/main" in block
    assert "environment: preview" in block
    assert "python -m pip install uv" in block
    assert "vercel pull --yes --environment=preview" in block
    assert "vercel build --yes" in block
    assert "postgresql://qbet:qbet@127.0.0.1:5432/qbet_build_only" in block
    assert "qbet-ci-build-only-secret" in block
    assert 'QBET_HOSTED_RUNTIME: "true"' in block
    assert "QBET_DJANGO_ALLOWED_HOSTS: q-bet.vercel.app" in block
    assert "QBET_DJANGO_ALLOWED_HOSTS: .vercel.app" not in block
    assert "Deploy protected Preview candidate" in block
    assert "vercel deploy --prebuilt --yes" in block
    assert 'vercel curl /health/ --deployment "$deployment_url" --silent' in block
    assert '"readiness": "migrations_pending"' in block
    assert "operator-controlled migration path" in block
    assert "vercel curl /accounts/login/" in block
    assert "vercel curl /static/qbet_web/app.css" in block
    assert "qbet-preview-smoke" in block
    assert "client.force_login(user)" in block
    assert "preview smoke identity must be active and non-privileged" in block
    assert 'Session.objects.filter(session_key=os.environ["SESSION_KEY"]).delete()' in block
    assert 'route in /dashboard/ /reports/ /portfolio/' in block
    assert "QBET_PREVIEW_SMOKE_USERNAME" not in block
    assert "QBET_PREVIEW_SMOKE_PASSWORD" not in block
    assert "vercel deploy --prebuilt --prod" not in block
    assert "manage.py migrate" not in block
    assert "psycopg.connect" not in block
    assert "qbet_polling_tick_token" not in block
    assert "QBET_MIGRATION_DATABASE_URL" not in block


def test_develop_cd_deploys_preview_only() -> None:
    block = _job("deploy", None)

    assert "github.event_name == 'push'" in block
    assert "refs/heads/develop" in block
    assert "refs/heads/main" in block
    assert "python manage.py migrate --check" not in block
    assert "manage.py migrate" not in block
    assert "Validate deployment configuration" in block
    assert "QBET_MIGRATION_DATABASE_URL" not in block
    assert "'production' || 'preview'" in block
    assert "qbet_polling_tick_token" not in block
    assert "vercel env run" not in block
    assert "psycopg.connect" not in block
    assert "QBET_POLLING_TICK_TOKEN" not in block
    assert "python -m pip install ." not in block
    assert "python-dotenv" not in block
    assert "python -m pip install uv" in block
    assert 'vercel pull --yes --environment="$VERCEL_ENVIRONMENT"' in block
    assert "- name: Deploy preview from develop\n        if: github.ref == 'refs/heads/develop'" in block
    assert "- name: Deploy production from main\n        if: github.ref == 'refs/heads/main'" in block
    assert "run: vercel build --token \"$VERCEL_TOKEN\"" in block
    assert "run: vercel build --prod --token \"$VERCEL_TOKEN\"" in block
    assert "vercel deploy --prebuilt --yes" in block
    assert "vercel deploy --prebuilt --prod --yes" in block
    assert "https://q-bet.vercel.app" in block
    assert "Verify production release health" in block
    assert "if: github.ref == 'refs/heads/main'" in block
    assert 'curl --fail --silent --show-error "$STABLE_URL/health/"' in block
    assert 'curl --fail --silent --show-error "$STABLE_URL/accounts/login/"' in block
    assert 'curl --fail --silent --show-error "$STABLE_URL/static/qbet_web/app.css"' in block
    assert "--env QBET_HOSTED_RUNTIME=true" in block
    assert "--env QBET_DJANGO_ALLOWED_HOSTS=q-bet.vercel.app" in block
    assert "--env QBET_DJANGO_ALLOWED_HOSTS=.vercel.app" not in block


def test_candidate_preflight_requires_quality_gates_before_deployment() -> None:
    block = _job("vercel-build-check", "deploy")

    assert "needs:\n      - validation\n      - performance" in block
    assert "!cancelled()" in block
    assert "needs.validation.result == 'success'" in block
    assert "needs.performance.result == 'success'" in block
    assert "github.event_name == 'pull_request'" in block
    assert "github.event.pull_request.head.repo.full_name == github.repository" in block
    assert "github.event_name == 'push'" in block
    assert "refs/heads/develop" in block
    assert "refs/heads/main" in block
    assert "vercel deploy --prebuilt --yes" in block


def test_hosted_deployment_routes_develop_to_preview_and_main_to_production() -> None:
    block = _job("deploy", None)

    assert "refs/heads/develop" in block
    assert "refs/heads/main" in block
    assert "name: ${{ github.ref == 'refs/heads/main' && 'production' || 'preview' }}" in block
    assert "vercel deploy --prebuilt --yes" in block
    assert "vercel deploy --prebuilt --prod --yes" in block
    assert "QBET_RELEASE_SHA: ${{ github.sha }}" in block
    assert "needs.vercel-build-check.result == 'success'" in block
    assert "always()" in block
    assert "qbet_build_only" in block
    assert "qbet-ci-build-only-secret" in block


def test_validation_includes_hosted_production_security_check() -> None:
    block = _job("validation", "performance")

    assert "Check hosted production security" in block
    assert 'QBET_HOSTED_RUNTIME: "true"' in block
    assert "VERCEL_ENV: production" in block
    assert "QBET_DJANGO_ALLOWED_HOSTS: q-bet.vercel.app" in block
    assert "python manage.py check --deploy" in block


def test_standard_validation_enforces_repository_coverage_contract() -> None:
    validation = _job("validation", "performance")
    performance = _job("performance", "vercel-build-check")
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    coverage = pyproject["tool"]["coverage"]
    assert coverage["run"]["source"] == ["src/qbet"]
    assert coverage["report"]["fail_under"] == 85
    assert coverage["report"]["show_missing"] is True
    assert sorted(coverage["run"]["omit"]) == [
        "src/qbet/storage/migrations/*",
        "src/qbet/web/migrations/*",
    ]

    assert "Run full standard Pytest suite with coverage" in validation
    assert "--cov=src/qbet" in validation
    assert "--cov-report=xml:coverage.xml" in validation
    assert "--durations=20" in validation
    assert "python -m coverage report" in validation
    assert "q-bet-coverage-${{ github.sha }}" in validation
    assert "if-no-files-found: ignore" in validation
    assert "continue-on-error: true" in validation

    assert "--cov=src/qbet" not in performance
    assert "coverage report" not in performance


def test_preview_migrations_are_manual_and_keep_database_secrets_in_vercel() -> None:
    assert "workflow_dispatch:" in PREVIEW_MIGRATIONS
    assert "pull_request:" not in PREVIEW_MIGRATIONS
    assert "push:" not in PREVIEW_MIGRATIONS
    assert "environment: preview" in PREVIEW_MIGRATIONS
    assert "vercel pull --yes --environment=preview" in PREVIEW_MIGRATIONS
    assert "vercel env run -e preview" in PREVIEW_MIGRATIONS
    assert "python manage.py migrate --plan" in PREVIEW_MIGRATIONS
    assert "python manage.py migrate --noinput" in PREVIEW_MIGRATIONS
    assert "python manage.py migrate --check" in PREVIEW_MIGRATIONS
    assert "QBET_DATABASE_URL" not in PREVIEW_MIGRATIONS
    assert "QBET_MIGRATION_DATABASE_URL" not in PREVIEW_MIGRATIONS
