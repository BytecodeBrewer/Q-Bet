# Bank Connector Evaluation

**Decision date:** 2026-09-01  
**Status:** Accepted for architecture; mock-only for provider access  
**Scope:** Evaluate the first official bank-connector path for Q-Bet. No account access, credentials, payment initiation, or bank movement was performed.

## Decision

Keep the bank layer mock-only for now. Do not select a production connector until the intended account type and the legal integration route are explicitly confirmed.

If Q-Bet later operates with a **Revolut Business** account, Revolut Business is the leading candidate for a narrowly scoped, read-only proof of concept because it has official sandbox support and documented account-management APIs. This is not a recommendation to open, connect, or fund an account. It is a conditional technical candidate only.

ING and Commerzbank remain viable PSD2/XS2A targets for a regulated third-party-provider integration, but are not viable first direct connectors for a personal Q-Bet installation without the required regulated-TPP status, customer consent, and provider onboarding.

## Provider Comparison

| Candidate | Official API and account fit | Auth, consent, and eligibility | Sandbox and test path | Read / payment capability | Decision |
| --- | --- | --- | --- | --- | --- |
| Revolut Business | Official Business API exists, but production access is for Revolut Business account holders. It is not evidence that a personal Revolut account can use that API. | Certificate-backed application setup, signed JWT/token flow, and Business-app consent. Production configuration can require IP allowlisting. | Official Sandbox supports mock data without real personal data and has a Business API simulation facility. | Documented account and transaction management; payment-related capabilities exist. Any write/payment scope must remain disabled by default. | Best conditional candidate if the user deliberately chooses a Revolut Business account. Otherwise mock-only. |
| Revolut Open Banking | Official Open Banking API exists for account, transaction, and payment use cases. Germany is listed in its EEA coverage. | Requires a regulated TPP and the appropriate eIDAS/OBIE certificate, or Revolut partnership for innovative-service-provider access. | No verified self-service path suitable for an unregulated personal project was found in the official material reviewed. | Account/transaction information and payment initiation are described, subject to provider eligibility and user consent. | Not a first direct Q-Bet connector. |
| ING Germany XS2A | ING states that its XS2A API permits account-data access and payment initiation. | Requires regulatory approval from a German or other European authority plus customer consent; ING also describes strong customer authentication. | ING's public pages point TPP developers to its portal, but this review did not verify a sandbox path usable without regulated-TPP onboarding. | AIS and PIS are available for eligible TPPs. | Not a first direct Q-Bet connector. |
| Commerzbank PSD2 / XS2A | Official PSD2 developer portal and a current XS2A endpoint exist. | The public portal is for PSD2/XS2A integration; this review did not establish a personal-account developer route or self-service access without TPP onboarding. | A usable unregulated sandbox route was not verified from the official material reviewed. | Regulatory interface availability is evidenced; exact account support, scope, limits, and onboarding must be confirmed with Commerzbank before any future integration. | Not selected; retain as a future regulated-TPP candidate. |

## Country And Account Assumptions

- The user is assumed to be in Germany, but no account type, ownership model, provider contract, or regulatory status is assumed.
- A personal retail account, a business account, and a PSD2 account-access relationship are distinct integration cases. They must not be treated as interchangeable.
- Q-Bet must not ask for online-banking passwords, TANs, app PINs, browser-session material, or personal API tokens as an implementation shortcut.

## Security, Compliance, And Retention

- Store no credential, refresh token, certificate private key, consent artifact, or bank identifier in domain models, logs, reports, test fixtures, or issue comments.
- A future credential store must be separate from the domain layer, encrypted, access-controlled, redacted in observability, and configured with explicit retention/deletion rules.
- Future production planning must confirm provider terms, German/EU regulatory obligations, data-processing roles, consent lifecycle, rate limits, fees, and account-specific transfer limits before enabling a connector.
- The currently researched public documentation did not provide a Q-Bet-specific rate-limit or price commitment. Treat both as unknown until verified during approved provider onboarding.

## Minimal Future Boundary

The first implementation remains provider-neutral and mock-backed. The connector should separate read operations from approval-gated funding operations:

```python
class BankConnector(Protocol):
    def get_balances(self) -> tuple[BankBalance, ...]: ...
    def list_transactions(
        self, query: TransactionQuery
    ) -> tuple[BankTransaction, ...]: ...


class FundingConnector(BankConnector, Protocol):
    def create_funding_request(
        self, request: FundingRequest, approval: UserApproval
    ) -> FundingRequestResult: ...
```

Rules for that future boundary:

- `get_balances` and `list_transactions` are read-only and must use explicit, least-privilege access.
- A funding request is only a request/preview until a fresh, explicit user approval is supplied at the GUI boundary.
- No automatic top-up, withdrawal, transfer, or credential enrollment is permitted.
- Mock connectors remain the default in tests, simulation, and local development.

## Follow-up Gate

Before opening a production-connector ticket, the user must choose one account type and explicitly approve provider onboarding. The ticket must then verify the selected provider's current legal eligibility, contract, sandbox, rate limits, fees, consent flow, and production scopes. Until that gate is passed, Q-Bet continues with mock balance and transaction data only.

## Official Sources

- [Revolut Business sandbox setup](https://developer.revolut.com/docs/guides/manage-accounts/get-started/prepare-sandbox-environment)
- [Revolut Business API reference](https://developer.revolut.com/docs/api/business)
- [Revolut Open Banking access controls](https://developer.revolut.com/docs/guides/build-banking-apps/introduction-to-the-open-banking-api/global-customer-access-controls)
- [ING Germany PSD2 and XS2A information](https://www.ing.de/hilfe/psd2/)
- [ING PSD2 API announcement](https://ing.com/news/2020/07/psd2-apis-now-available-on-ing-developer-portal.html)
- [Commerzbank PSD2 developer portal](https://psd2.developer.commerzbank.com/)