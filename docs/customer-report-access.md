# Customer Report Access

Customer result reports use an explicit per-user access grant. A normal
authenticated user can read only reports with a matching `CustomerReportAccess`
record. Staff can inspect all reports for support and administration.

The access check is applied before report detail and every CSV, JSON, and PDF
export. The history page filters to the same grants, so a user cannot infer the
existence of another user's report through a list or an export URL.

When the current administrator-only deterministic Simulation control starts a
report, it creates an access grant for its initiating user. Administrators can
manage future assignments through Django Admin. Production engine or execution
flows must create the grant at their own authenticated ownership boundary; they
must not rely on report contents or technical identifiers as authorization.
