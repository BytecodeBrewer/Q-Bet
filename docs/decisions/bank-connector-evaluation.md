# Financial-Account Connector Evaluation

**Decision date:** 2026-09-01<br>
**Status:** Mock-only pending a user-selected account and provider-onboarding route<br>
**Scope:** A lawful, documentation-only evaluation. No account access, credential, consent, payment, transfer, top-up, or bank movement was performed.

## Decision

Keep Q-Bet's financial-account layer mock-only. A production connector is not selected because the user's intended account type, ownership model, and provider contract are unknown.

The broad screening identifies **bunq** as the strongest conditional first technical candidate if the user chooses a bunq personal or business account: it has an official API and open sandbox that does not require a real account. **Revolut Business** remains a strong alternative only for a user who intentionally holds a Revolut Business account. Neither statement authorizes account creation, onboarding, credential collection, or money movement.

A direct personal-project path to ING, Commerzbank, or Revolut Open Banking is not selected: their documented PSD2/Open-Banking route is for regulated third-party providers (TPPs) with customer consent. **finAPI** is a plausible commercial open-banking intermediary, but it is not an account provider and requires separate business/contractual evaluation. **Wise** is a credible account platform with a sandbox, but its EU/UK small-business personal API token has documented restrictions on funding transfers and viewing balance statements; it is therefore not the first Q-Bet connector.

## Candidate Types

| Type | Meaning in this decision |
| --- | --- |
| Account provider | The user can open/hold the relevant payment account with the provider; an API may still require a product-specific contract. |
| Regulated TPP interface | A bank exposes account/payment APIs to licensed AISPs/PISPs; this does not give an ordinary app direct access to a user's account. |
| Open-banking intermediary | A commercial provider aggregates bank access or payment initiation for client applications; it is not the user's bank account. |

## Broad Discovery Table

`Supported` means official material confirms the capability category. `Restricted` means a documented eligibility or product limitation applies. `Unverified` means the reviewed official material does not establish a Q-Bet-specific entitlement. All funding operations remain approval-gated even where technically supported.

| Candidate | Type and account model | Official API / sandbox | Read access | Payment initiation / transfer | Deposit / top-up | Why shortlisted or rejected |
| --- | --- | --- | --- | --- | --- | --- |
| [bunq](https://doc.bunq.com/basics/getting-started) | Account provider; personal or business account in production. | Official API; open sandbox with mock users and money. | Supported in API concept; production entitlement needs the selected bunq account/API permission. | Supported in the documented API/sandbox payment flow; production use requires account/API authorization. | Sandbox simulation supported; production top-up entitlement and limits must be confirmed during onboarding. | **Shortlisted.** Strong test path and direct account-provider model. |
| [Revolut Business](https://developer.revolut.com/docs/guides/manage-accounts/get-started/sign-up-for-revolut-business-account) | Account provider; Business only for its Business API. | Official Business API and mock-data sandbox. | Supported for an eligible Business account. | Supported API category; payment scope must remain disabled until an explicit approval flow exists. | Sandbox simulations include top-up/transfer state changes; production details are account/contract dependent. | **Shortlisted conditionally.** Good sandbox, but not a personal-account route. |
| [Wise](https://docs.wise.com/guides/developer/environments) | Account provider/platform; personal and business profiles exist. | Official sandbox and simulations. | Restricted for EU/UK small-business personal API tokens: balance statements cannot be viewed by that token. | Restricted for that token: it cannot fund transfers in EU/UK; partner/OAuth models require onboarding. | Sandbox supports simulated top-ups; production top-up/funding is not established for Q-Bet. | **Shortlisted as alternative, not first.** Clear sandbox but product/auth restrictions matter. |
| [finAPI](https://www.finapi.io/en/home/) | Open-banking intermediary; not an account provider. | Official API products and XS2A sandbox. | Supported service category through finAPI's account-information offering, subject to commercial/consent route. | Supported service category through payment-initiation offering, subject to contract and consent. | Unverified as a distinct capability; do not infer it from payment initiation. | **Shortlisted as commercial route.** Useful only after separate business, privacy, and contract review. |
| [ING Germany XS2A](https://www.ing.de/hilfe/psd2/) | Regulated TPP interface for eligible third parties; not a general personal developer API. | Developer portal is referenced; an unregulated self-service sandbox was not verified. | Supported for authorized TPPs with customer consent. | Supported for authorized TPPs with customer consent. | Unverified as a distinct API operation. | **Rejected for first direct connector.** Regulatory eligibility is a blocker. |
| [Commerzbank PSD2 / XS2A](https://psd2.developer.commerzbank.com/) | Regulated TPP interface; personal-account developer access not established. | Official portal/XS2A service exists; unregulated self-service sandbox not verified. | Regulatory service exists; Q-Bet entitlement is unverified. | Regulatory service exists; Q-Bet entitlement is unverified. | Unverified as a distinct API operation. | **Rejected for first direct connector.** TPP/onboarding details must be confirmed first. |
| [Revolut Open Banking](https://developer.revolut.com/docs/guides/build-banking-apps/introduction-to-the-open-banking-api/global-customer-access-controls) | Regulated TPP/partner interface, separate from Revolut Business API. | Official Open Banking API; no self-service unregulated path verified. | Supported for regulated TPPs/partners under documented access controls. | Supported for regulated TPPs/partners under documented access controls. | Unverified as a distinct API operation. | **Rejected for first direct connector.** eIDAS/TPP or partnership is required. |

## Detailed Viable Shortlist

### 1. bunq - conditional first technical candidate

- **Country/account assumption:** a user-selected bunq personal or business account is required for production. The official getting-started guide states that production API use needs such an account, while sandbox use does not. [Source](https://doc.bunq.com/basics/getting-started)
- **Authentication and scopes:** API-key and permission details must be confirmed against the selected production product before implementation; do not collect a key in Q-Bet domain models or logs.
- **Sandbox:** open sandbox with generated users, fake money, and payment simulation is documented. [Source](https://doc.bunq.com/basics/getting-started)
- **Read access:** technically plausible through the official API, but contractually unverified for the user's chosen product until onboarding.
- **Funding operations:** transfer/payment capability is documented; deposit and top-up are separate operations and remain unverified for a Q-Bet production account. Every non-read operation needs fresh explicit user approval.
- **Rate limits, costs, limits:** unverified for the selected product; obtain them only in approved provider onboarding.
- **Verdict:** technically promising; contractually pending. Keep mock-only until the user chooses this provider/account route.

### 2. Revolut Business - conditional business-account alternative

- **Country/account assumption:** its Business API is available in production only to Revolut Business account holders. It must not be treated as a Revolut personal-account API. [Source](https://developer.revolut.com/docs/guides/manage-accounts/get-started/sign-up-for-revolut-business-account)
- **Authentication and scopes:** documented setup includes application certificate, signed JWT/token flow, Business-app consent, and potentially production IP allowlisting. [Source](https://developer.revolut.com/docs/api/business)
- **Sandbox:** mock data can be used without real personal data; Business API simulations are sandbox-only. [Source](https://developer.revolut.com/docs/guides/manage-accounts/get-started/prepare-sandbox-environment) [Source](https://developer.revolut.com/docs/api/business)
- **Read access:** technically supported for eligible Business accounts.
- **Funding operations:** payment capabilities exist, but the specific transfer, deposit, and top-up operations and their production scopes must be selected and approved separately. No `PAY`-like scope belongs in an initial read-only connector.
- **Rate limits, costs, limits:** unverified for the user's future Business contract.
- **Verdict:** technically viable, contractually pending a deliberate Business-account choice.

### 3. Wise - account-provider alternative with material restrictions

- **Country/account assumption:** Wise has personal/business profiles and distinguishes small-business self-automation from partner integrations. [Source](https://docs.wise.com/guides/product/kyc/partner-accounts)
- **Authentication and scopes:** small-business users may use a personal API token; partners/enterprises use OAuth 2.0 and onboarding. [Source](https://docs.wise.com/guides/product/kyc/partner-accounts)
- **Sandbox:** official sandbox supports test requests, simulated top-ups, transfer state changes, statements, and webhooks; it never moves real money. [Source](https://docs.wise.com/guides/developer/environments)
- **Read access:** technically available in platform models, but EU/UK small-business personal API tokens cannot view balance statements. [Source](https://docs.wise.com/guides/product/kyc/partner-accounts)
- **Funding operations:** the same token cannot fund transfers in EU/UK. Partner/OAuth entitlement must be contractually verified before considering payment initiation or transfers. Deposit and top-up remain separate and unverified for Q-Bet production.
- **Rate limits, costs, limits:** integration model and scopes are set in onboarding; no Q-Bet-specific commitment is established. [Source](https://docs.wise.com/guides/developer)
- **Verdict:** useful for a future approved commercial integration, not the first direct connector.

### finAPI - commercial open-banking route, not a replacement account

- **Type:** finAPI is an intermediary that offers account-information and payment-initiation services for companies; it does not provide the user account Q-Bet would monitor. [Source](https://www.finapi.io/en/home/)
- **Authentication, consent, and compliance:** its XS2A documentation states that direct XS2A access requires PSP registration and a PSD2-compliant certificate; customer-facing flows are consent/privacy-sensitive. [Source](https://documentation.finapi.io/xs2a/01-getting-started) [Source](https://live.finapi.io/dpp?lang=en&version=V2.0)
- **Sandbox:** XS2A sandbox/test data are documented, but this does not establish production commercial access for Q-Bet. [Source](https://documentation.finapi.io/xs2a/01-getting-started)
- **Read/payment/funding:** account information and payment initiation are supported product categories; transfer, deposit, and top-up must each be validated by product contract and user-consent flow. [Source](https://documentation.finapi.io/webform/web-form-2-0-basics)
- **Verdict:** potential future aggregation route after commercial, privacy, and approval design; not a first self-hosted connector.

## Regulatory Open-Banking Alternatives

ING requires a relevant German or European supervisory authorization plus customer consent to access account data or initiate payments through XS2A. [Source](https://www.ing.de/hilfe/psd2/)

Revolut Open Banking requires a regulated TPP and region-appropriate eIDAS/OBIE certificate; its German EEA coverage does not remove that requirement. [Source](https://developer.revolut.com/docs/guides/build-banking-apps/introduction-to-the-open-banking-api/global-customer-access-controls)

Commerzbank exposes a regulatory PSD2 developer portal, but this review did not verify an ordinary personal-project production entitlement. [Source](https://psd2.developer.commerzbank.com/)

These routes may be revisited only under an explicitly approved regulated-TPP or intermediary contract, never through browser automation or banking-login collection.

## Security, Compliance, And Retention

- Store no credential, refresh token, private key, consent artifact, account number, or bank identifier in domain models, logs, reports, test fixtures, or issue comments.
- Keep any future credential store outside the domain layer, encrypted, access-controlled, redacted in observability, and governed by explicit retention/deletion policy.
- Confirm current provider terms, scopes, pricing, rate limits, geographic coverage, consent expiry, and data-processing roles during approved onboarding. None is implied by this research record.

## Minimal Follow-up Boundary

```python
class BankConnector(Protocol):
    def get_balances(self) -> tuple[BankBalance, ...]: ...
    def list_transactions(self, query: TransactionQuery) -> tuple[BankTransaction, ...]: ...

class FundingConnector(BankConnector, Protocol):
    def create_funding_request(
        self, request: FundingRequest, approval: UserApproval
    ) -> FundingRequestResult: ...
```

- `BankConnector` is read-only and uses least-privilege access.
- `FundingConnector` creates a request/preview only. A fresh explicit GUI approval is required before any transfer, payment, deposit, or top-up action.
- Withdrawal, transfer, deposit, and top-up are distinct capability checks; support for one never implies support for another.
- Mock connectors are the only permitted default for local development, tests, and simulation.

## Follow-up Gate

Before a production-connector ticket, the user must select one provider and account type, approve onboarding, and confirm its current contract, country coverage, auth/scopes, rate limits, fees, consent lifecycle, data retention, and every desired money-operation capability. Until then, Q-Bet remains mock-only.
