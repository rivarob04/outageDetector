# Adding a provider to the catalog

1. **Find the official status page.** Look for a machine-readable feed:
   - Atlassian Statuspage and many look-alikes (including incident.io pages): `https://<page>/api/v2/summary.json`. Use `adapter: statuspage`.
   - Status.io: `https://api.status.io/1.0/status/<page id>` (the page id is in the page's HTML). Use `adapter: statusio`.
   - Better Stack and others: they usually have an RSS feed (`/feed.rss`). Use `adapter: rss`.
   - Bespoke APIs (AWS, Google Cloud, Google Workspace, Slack, Apple, Heroku) have their own adapters.
   - No feed at all: leave `feed` out. The service becomes **checks only**.
2. **Create `catalog/<id>.yaml`.** The file name must equal the `id`:

   ```yaml
   id: example
   name: Example
   category: devtools        # cloud, cdn, devtools, ai, comms, productivity, payments, streaming, gaming, social, smarthome, network
   icon: example             # a slug from https://simpleicons.org (optional)
   homepage: https://example.com
   status_page: https://status.example.com
   feed:
     adapter: statuspage
     url: https://status.example.com/api/v2/summary.json
     components_include: []  # optional regexes to only follow some components
     regions: auto           # or {"(?i)frankfurt": "aws:eu-central-1"}
   probes:
     - kind: http
       url: https://example.com
       accept_status: [403]  # 2xx/3xx always count as up; add codes like 401 for APIs or 403 for bot-protected sites
     - kind: dns
       host: example.com
   depends_on: [cloudflare]   # optional; region-specific: aws:us-east-1
   depends_on_sources: ["why we believe this (link)"]
   ```
3. **Verify it live:** `pulse catalog verify --only example --write --record backend/tests/fixtures/feeds/live`. You can also push to a `verify/<name>` branch and let the **Catalog verify** workflow do it from GitHub's network. Don't guess URLs from memory.
4. **Run `make test`.** The contract tests parse every recorded fixture with its adapter.
5. Update `CHANGELOG.md` and open a PR.

## Writing a new adapter
- Add `backend/pulse/adapters/<kind>.py` with `parse(content: bytes, feed: FeedConfig) -> FeedSnapshot`. It must be a pure function.
- Register it in `pulse/adapters/__init__.py` and the `AdapterKind` literal in `pulse/catalog/schema.py`.
- Record a real payload and add tests in `tests/test_adapters.py`, including a "derived" variant that shows an active incident (copy the real payload and flip the fields).
