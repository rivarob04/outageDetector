# Vantage agents

A Pulse **agent** runs your hub's checks from somewhere else and pushes the results back. With one or more agents, Pulse can tell:

- **Widespread**: the checks fail from your network *and* from agents. It's probably a real outage, even before the provider admits it.
- **Only from your network**: the checks fail at home but agents reach the service fine. It's your ISP, DNS, or a route.

## Set one up
1. In the hub, open **Settings → Vantage agents → Create agent**. Give it a name and, optionally, coordinates for the map. Copy the token: it's shown once.
2. On the remote machine, run `deploy/docker-compose.agent.yml` (or the snippet the UI shows) with `PULSE_HUB_URL` set to the hub's public URL and `PULSE_AGENT_TOKEN` set to the token.
3. The agent appears as **reporting** within a minute and shows up on the map.

The hub must be reachable from the agent over HTTPS (a reverse proxy or Cloudflare Tunnel works; see docs/REVERSE_PROXY.md). The agent never needs inbound ports.

## What agents run
- Every HTTP, DNS, TCP and TLS check of the services the hub watches.
- **Not** checks of your own services that point at private or LAN names (`192.168.x.x`, `*.local`, single-label hosts). Agents also always apply the SSRF guard to user-defined checks.
- Agents run their own connectivity canaries. An offline agent reports itself as offline and is excluded from verdicts instead of making everything look down.

## Security
- Requests are signed with HMAC-SHA256 over the timestamp, nonce, method, path and body hash. The hub rejects clock skew over 5 minutes, reused nonces (10-minute window), revoked agents and bad signatures.
- Per-agent secrets are derived from `PULSE_SECRET_KEY`, so no agent secret is stored. **Rotating `PULSE_SECRET_KEY` invalidates every agent token** (and every login session).
- Revoke a single agent in Settings; its token stops working immediately.

## Staleness
An agent counts as **fresh** when it reported within 3 × the push interval (default 60 s, plus 30 s grace). Stale, offline and revoked agents never influence verdicts.
