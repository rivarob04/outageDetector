# Deploying with Dockhand

Dockhand deploys image-based compose files, so use the files in `deploy/dockhand/` rather than `deploy/docker-compose.yml`, which builds from source.

| File | Use it for |
|---|---|
| `deploy/dockhand/compose.yaml` | Pulse on its own (SQLite, data in a named volume) |
| `deploy/dockhand/compose.mqtt.yaml` | Pulse plus a Mosquitto broker for Home Assistant |
| `deploy/dockhand/agent.compose.yaml` | A vantage agent on another machine |

## 1. Make sure the image exists
The **Image** workflow publishes `ghcr.io/rivarob04/outagedetector:edge` (amd64 + arm64) on every push to `main`. You can also run it by hand from **Actions → Image → Run workflow**. Each build is also tagged `sha-<commit>` if you want to pin one. Because the repository is private, the image is private too.

## 2. Let Dockhand pull from GHCR
1. On GitHub, create a **personal access token (classic)** with only the `read:packages` scope (Settings → Developer settings → Personal access tokens).
2. In Dockhand, go to **Settings → Registries → Add**:
   - Name: `GitHub Container Registry`
   - URL: `https://ghcr.io`
   - Username: your GitHub username
   - Password: the token
3. Click **Test connection**.

(Alternatively, make the package public: GitHub → your profile → Packages → outagedetector → Package settings → Change visibility.)

## 3. Create the stack
Pick one of these:

**A. Paste:** **Stacks → Create**, name it `pulse`, and paste the contents of `deploy/dockhand/compose.yaml`.

**B. From Git** (redeploys when the file changes): **Stacks → From Git**.
- Repository: `https://github.com/rivarob04/outageDetector.git`, with a token that has `repo` read access (the repository is private).
- Branch: `main`
- Compose file path: `deploy/dockhand/compose.yaml`

## 4. Set stack variables
All of them are optional. These are the ones worth setting:

| Variable | Example | Why |
|---|---|---|
| `PULSE_ADMIN_USERNAME` / `PULSE_ADMIN_PASSWORD` | `admin` / a long password | Creates the admin on first start. Without them, use the setup code from the container log. |
| `PULSE_BASE_URL` | `https://pulse.example.com` | Links in alerts; needed for agents |
| `PULSE_PORT` | `8080` | Host port |
| `PULSE_ALLOW_PRIVATE_TARGETS` | `true` | Lets you check LAN services such as `192.168.x.x` |
| `PULSE_COOKIE_SECURE` | `true` | When served over HTTPS |
| `PULSE_HOST_LOCATION` | `43.65,-79.38` | Puts this hub on the map |
| `PULSE_TAG` | `edge` or `sha-5fa6695` | Image tag |
| `TZ` | `America/Toronto` | Container time zone (alert quiet hours use each rule's own time zone) |

Every other setting is listed in [CONFIGURATION.md](CONFIGURATION.md). Add any of them under `environment:` in the stack.

## 5. Deploy and sign in
1. Deploy the stack, then open `http://<host>:8080`.
2. If you didn't set the admin variables, open the stack's **Logs** and look for "use setup code …". Then click **Set up admin** in Pulse and enter that code.
3. Go to **Add services** and watch what you depend on.

## Updating
`pull_policy: always` makes each redeploy pull the newest image for the tag. In Dockhand, **redeploy** the stack, or turn on auto-update for Git stacks. Database migrations run automatically on start. For production, pin `PULSE_TAG` to a `sha-…` (or, after a release, a version) and change it on purpose.

## Behind a reverse proxy
If Dockhand's host already runs Traefik, Caddy or Nginx Proxy Manager, remove the `ports:` lines and put Pulse on the proxy's network. See [REVERSE_PROXY.md](REVERSE_PROXY.md). Server-sent events at `/api/v1/events` must not be buffered.

## Agents
On another Docker host managed by Dockhand (Dockhand can manage remote hosts), create a stack from `deploy/dockhand/agent.compose.yaml`. Set `PULSE_HUB_URL` and `PULSE_AGENT_TOKEN` (from **Settings → Vantage agents** in Pulse). That host also needs the GHCR registry from step 2.

## Backups
The data lives in the `pulse_pulse-data` volume. You can back up the volume, or run `pulse backup /data/backup.tar.gz` from the container's console in Dockhand.
