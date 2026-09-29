from pathlib import Path


WORKFLOW = (Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml").read_text(
    encoding="utf-8"
)


def _job(name: str, next_name: str | None = None) -> str:
    start = WORKFLOW.index(f"  {name}:\n")
    end = WORKFLOW.index(f"  {next_name}:\n", start) if next_name else len(WORKFLOW)
    return WORKFLOW[start:end]


def test_pull_request_vercel_gate_builds_without_deploying() -> None:
    block = _job("vercel-build-check", "deploy-develop")

    assert "github.event_name == 'pull_request'" in block
    assert "github.event.pull_request.head.sha" in block
    assert "runs-on: ubuntu-22.04" in block
    assert "python -m pip install uv" in block
    assert "vercel pull --yes --environment=preview" in block
    assert "vercel build --yes" in block
    assert "postgresql://qbet:qbet@127.0.0.1:5432/qbet_build_only" in block
    assert "qbet-ci-build-only-secret" in block
    assert "vercel deploy" not in block
    assert "qbet_polling_tick_token" not in block
    assert "QBET_MIGRATION_DATABASE_URL" not in block


def test_develop_cd_preflights_schema_and_updates_stable_alias() -> None:
    block = _job("deploy-develop")

    assert "github.event_name == 'push'" in block
    assert "refs/heads/develop" in block
    assert "python manage.py migrate --check" in block
    assert "python -m pip install uv" in block
    assert "vercel pull --yes --environment=production" in block
    assert "QBET_HOSTED_PREVIEW: \"false\"" in block
    assert "vercel build --prod" in block
    assert "vercel deploy --prebuilt --prod --yes" in block
    assert "qbet_polling_tick_token" in block
    assert "https://q-bet.vercel.app" in block
    assert '"release": "$EXPECTED_RELEASE"' not in block
    assert 'grep -Fq "\\\"release\\\": \\"$EXPECTED_RELEASE\\\""' in block
