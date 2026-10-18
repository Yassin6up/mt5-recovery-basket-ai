# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems (for example an HMAC/authentication
bypass, replay attack, secret leak, or a way to make the EA trade outside its risk limits).

Report it privately using GitHub's **"Report a vulnerability"** button on the repository's
*Security* tab (private vulnerability reporting). Include steps to reproduce and the affected
version or commit. You should receive a response as soon as the maintainer is able to review it.

## Scope reminders

* The decision service must only listen on loopback, or behind TLS with an IP allow-list.
* Secrets (HMAC key, broker credentials) must never be committed, logged, or sent to an AI provider.
* This software comes with no warranty (MIT License). Run it on demo accounts.
