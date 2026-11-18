# Security

Report vulnerabilities privately through GitHub's **Security → Report a vulnerability** on this repository. Please don't open public issues for them.

## Threat model

| Asset | Threats | Mitigations |
|---|---|---|
| **Admin access** (watching services, alert URLs, agent tokens) | Password guessing; session theft; CSRF | scrypt password hashes; 5 failed logins per IP per 5 minutes; signed `HttpOnly` `SameSite=Lax` session cookie (`Secure` + HSTS with `PULSE_COOKIE_SECURE=true`); a CSRF token is required on every state-changing request made with a cookie; first-run admin creation requires a one-time setup code from the logs. |
| **Trusted-header auth** | A forged `Remote-User` header | The header is only accepted from `PULSE_TRUSTED_PROXIES`, judged by the **TCP peer address** (not `X-Forwarded-For`). The default leaves out LAN ranges. Expose Pulse only through the proxy. |
| **Your network** (SSRF) | User-defined checks probing internal hosts or cloud metadata | User-defined (custom) checks resolve the name first and refuse private, loopback, link-local, CGNAT and metadata addresses unless `PULSE_ALLOW_PRIVATE_TARGETS=true`. Every redirect hop is re-checked. After connecting, the **actual peer address** is checked as well (defeats DNS rebinding). Check results never return response bodies, only status, timing and error class. Catalog checks are fixed URLs from the repo. |
| **Agent channel** | Forged or replayed results; stolen tokens | HMAC-SHA256 over timestamp, nonce, method, path and body hash; ±5-minute clock window; nonces remembered for 10 minutes (replays → 409); per-agent revocation; secrets derived from `PULSE_SECRET_KEY` (no secrets at rest; rotating the key revokes all). Results can't exceed 2,000 per request, can only reference known checks, and timestamps outside −2 h/+5 min are clamped. Agents never receive private custom targets. |
| **Alert channel URLs** (contain tokens) | Disclosure via the API or exports | Stored server-side only; masked in the UI and API; excluded from config exports. Webhooks are signed (`X-Pulse-Signature`). |
| **The browser** | XSS; clickjacking | React escapes all content; incident text is stripped to plain text server-side. Strict CSP (`script-src 'self'`, no inline scripts), `X-Frame-Options: DENY` / `frame-ancestors 'none'`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`. |
| **Feed parsing** | Malicious XML/JSON from a compromised status page | `defusedxml` for RSS/Atom (no entities or external DTDs); JSON only via the standard library; payloads are pure data and are never evaluated. |
| **The container** | Escalation after a compromise | Runs as UID 10001, read-only root filesystem (only `/data` and `/tmp` are writable), `cap_drop: [ALL]`, `no-new-privileges`, and no ICMP/raw sockets needed. The image is scanned with Trivy in CI. |
| **Logs** | Secret leakage | No secrets are logged. Structured log fields whose names contain password, secret, token, authorization or cookie are redacted. The only credential ever logged is the one-time setup code, which works only while no admin exists. |

## Hardening checklist
- Put Pulse behind HTTPS (see docs/REVERSE_PROXY.md) and set `PULSE_COOKIE_SECURE=true` and `PULSE_BASE_URL`.
- Set `PULSE_PUBLIC_VIEW=false` if the instance is reachable from the internet and you don't want anonymous viewing. Use the public `/status` page for sharing instead.
- Keep `PULSE_ALLOW_PRIVATE_TARGETS=false` unless you monitor homelab services.
- Set `PULSE_METRICS_TOKEN` if `/metrics` is reachable by others.
- Back up `/data` (or use `pulse backup`), and keep `PULSE_SECRET_KEY` safe.

## Known limitations
- A single admin account (no roles). Anyone with admin access is trusted to define checks.
- Rate limiting covers logins only. Put an internet-facing instance behind a proxy with request limits.
