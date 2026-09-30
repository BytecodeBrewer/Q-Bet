from pathlib import Path


WORKFLOW = (Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml").read_text(
    encoding="utf-8"
)


def _job(name: str, next_name: str | None = None) -> str:
    start = WORKFLOW.index(f"  {name}:\n")
    end = WORKFLOW.index(f"  {next_name}:\n", start) if next_name else len(WORKFLOW)
    return WORKFLOW[start:end]


def test_pull_request_vercel_gate_builds_without_deploying() -> None:
    block = _job("vercel-build-check", "deploy")

    assert "      - develop\n      - main" in WORKFLOW
    assert "!cancelled()" in block
    assert "github.event_name == 'pull_request'" in block
    assert "github.event.pull_request.head.sha" in block
    assert "refs/heads/develop" in block
    assert "environment: preview" in block
    assert "python -m pip install uv" in block
    assert "vercel pull --yes --environment=preview" in block
    assert "vercel build --yes" in block
    assert "postgresql://qbet:qbet@127.0.0.1:5432/qbet_build_only" in block
    assert "qbet-ci-build-only-secret" in block
    assert 'QBET_HOSTED_RUNTIME: "true"' in block
    assert "QBET_DJANGO_ALLOWED_HOSTS=q-bet.vercel.app" in block
    assert "QBET_DJANGO_ALLOWED_HOSTS=.vercel.app" not in block
    assert 'QBET_HOSTED_RUNTIME: "true"' in block
    assert "QBET_DJANGO_ALLOWED_HOSTS: q-bet.vercel.app" in block
    assert "QBET_DJANGO_ALLOWED_HOSTS: .vercel.app" not in block
    assert "vercel deploy" not in block
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


def test_pull_request_vercel_build_runs_after_failed_quality_gates() -> None:
    block = _job("vercel-build-check", "deploy")

    assert "needs:\n      - validation\n      - performance" in block
    assert "!cancelled()" in block
    assert "github.event_name == 'pull_request'" in block
    assert "github.event.pull_request.head.repo.full_name == github.repository" in block
    assert "github.event_name == 'push'" in block
    assert "refs/heads/develop" in block
    assert "vercel deploy" not in block


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
