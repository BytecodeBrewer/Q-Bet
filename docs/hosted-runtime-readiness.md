# Hosted Runtime Readiness

Q-Bet distinguishes a successful web deployment from an environment that is ready for interactive product acceptance.

## Product-ready contract

The hosted `/health/` endpoint is the product-readiness boundary. A ready environment returns HTTP 200 and reports:

- `status=ok`;
- `persistence=ready`;
- `readiness=ready`;
- Routing, Approval persistence and Monitoring persistence as `ready`;
- the immutable Git release SHA deployed by CI.

A deployment that responds to HTTP requests but reports pending migrations or an unavailable durable read path is not product-ready.

## Exact release parity

GitHub Actions checks out the exact pull-request head SHA before deploying a Vercel preview. The same SHA is injected as `QBET_RELEASE_SHA` and returned by `/health/`.

The preview smoke test rejects a deployment whose reported release does not match the reviewed GitHub head. This prevents reviewing one commit while exercising another deployment.

## Pending migrations

Normal pull-request CI must never mutate the shared hosted PostgreSQL schema.

If the deployment reports `persistence=migrations_pending`, the Vercel deployment may still exist for inspection, but CI marks it explicitly as not product-ready. Strict application smoke checks are skipped until an operator applies the intended migrations.

Database unavailability, invalid migration state, or unavailable Routing, Approval or Monitoring persistence remain blocking readiness failures.

## Operator-owned migration flow

Hosted schema changes use the separate **Hosted database migration** GitHub Actions workflow. It is available only through `workflow_dispatch` and requires:

1. a protected GitHub Environment, currently `staging` or `production`;
2. the exact 40-character Git release SHA whose migrations are being applied;
3. the literal confirmation `MIGRATE`;
4. environment secret `QBET_MIGRATION_DATABASE_URL` pointing to the intended PostgreSQL database;
5. environment secret `QBET_DJANGO_SECRET_KEY`.

The workflow checks out the exact requested release, prints the Django migration plan, applies `python manage.py migrate --noinput`, then requires `python manage.py migrate --check` and `python manage.py check` to pass.

The workflow does not run automatically on pull requests, pushes, web requests or serverless cold starts.

After the migration completes, re-check the deployed `/health/` endpoint. Product acceptance should proceed only after the deployment reports the ready contract above.

## Safety boundary

Runtime-readiness probes are read-only. They do not:

- approve or reject Execution work;
- expire approvals;
- mutate PortfolioLedger state;
- append Monitoring records;
- start or stop engines;
- call bank or provider APIs.

Existing fail-closed behavior remains authoritative whenever durable state cannot be read.
