# Running behind a reverse proxy

Pulse serves its UI, API and live updates (server-sent events at `/api/v1/events`) on one port (8080). Whatever you use:
- **Don't buffer** `/api/v1/events`. Pulse sends `X-Accel-Buffering: no`, which Nginx honours.
- Set `PULSE_BASE_URL=https://pulse.example.com` and `PULSE_COOKIE_SECURE=true`.
- If agents report to this hub, it must be reachable from them over HTTPS.

## Traefik (labels)
```yaml
services:
  pulse:
    # ...as in deploy/docker-compose.yml, but without "ports:"
    labels:
      - traefik.enable=true
      - traefik.http.routers.pulse.rule=Host(`pulse.example.com`)
      - traefik.http.routers.pulse.entrypoints=websecure
      - traefik.http.routers.pulse.tls.certresolver=letsencrypt
      - traefik.http.services.pulse.loadbalancer.server.port=8080
    networks: [traefik]
```

## Caddy
```caddy
pulse.example.com {
    reverse_proxy pulse:8080 {
        flush_interval -1   # stream server-sent events immediately
    }
}
```

## Nginx Proxy Manager
Add a proxy host for `pulse.example.com` → `pulse:8080`. Turn on **Websockets Support** (harmless) and SSL. Under **Advanced**, add:
```nginx
location /api/v1/events {
    proxy_pass http://pulse:8080;
    proxy_buffering off;
    proxy_read_timeout 1h;
}
```

## Cloudflare Tunnel
```yaml
services:
  cloudflared:
    image: cloudflare/cloudflared:latest
    command: tunnel --no-autoupdate run
    environment:
      TUNNEL_TOKEN: ${CF_TUNNEL_TOKEN}
```
In the tunnel's public hostname, point `pulse.example.com` at `http://pulse:8080`. Add **Cloudflare Access** and set `PULSE_TRUSTED_AUTH_HEADER=Cf-Access-Authenticated-User-Email` to use Cloudflare's login for the admin UI. Make sure port 8080 isn't published elsewhere.

## Authelia / Authentik (forward auth)
Protect the hostname with your forward-auth middleware and set `PULSE_TRUSTED_AUTH_HEADER=Remote-User`. To let agents and the public status page through, exclude these paths from authentication: `/api/v1/agent/*` (requests are HMAC-signed), `/status`, `/status.json`, and optionally `/badge/*` and `/feed/*`.
