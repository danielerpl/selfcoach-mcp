# Remote mode: claude.ai, the Claude mobile apps and any device

By default selfcoach runs **locally**: your MCP client starts it on your computer. That works for
Claude Code, Claude Desktop and Cursor, but not for claude.ai in a browser or the Claude mobile
apps, which can only reach servers on the internet.

Remote mode runs the same server on an always-on machine you control (home server, Raspberry Pi,
NAS, small VPS), reachable over HTTPS, protected by OAuth. Once you add it as a **custom connector**
on claude.ai, it works in the browser, the desktop app and the iOS/Android apps.

```
 phone / browser / desktop                Anthropic                  your server
 ─────────────────────────               ─────────                  ───────────
 Claude app ──────────────────▶ claude.ai connector ── HTTPS ──▶ Cloudflare Tunnel
                                                                       │
                                                       127.0.0.1:8765  selfcoach-mcp serve --http
                                                                       │
                                                                       ▼
                                                                Garmin Connect
```

- [How access is protected](#how-access-is-protected)
- [1. Install on the server](#1-install-on-the-server)
- [2. Run it as a service](#2-run-it-as-a-service)
- [3. Publish it with Cloudflare Tunnel](#3-publish-it-with-cloudflare-tunnel)
- [4. Connect your clients](#4-connect-your-clients)
- [5. Maintenance](#5-maintenance)
- [Troubleshooting](#troubleshooting)

## How access is protected

Remote mode is **single-user**: the server holds one Garmin account, yours.

- **Bearer tokens on every request.** Every request to `/mcp` needs an OAuth access token. Without
  one the server answers `401`.
- **Passphrase to connect a client.** To get a token, a client (claude.ai, Claude Code…) opens a
  login page on your server, and you type the passphrase from `SELFCOACH_PASSPHRASE`. You do this
  once per client.
- **Only trusted redirect hosts.** Clients register themselves automatically (OAuth dynamic client
  registration), but only with redirect addresses on `claude.ai`, `claude.com` or `localhost`. To
  allow other apps, set `SELFCOACH_OAUTH_REDIRECT_HOSTS`.
- **Lockout on wrong passphrases.** After 10 wrong attempts in 15 minutes the login is locked.
- **Token lifetimes.** Access tokens last 24 h and refresh tokens 180 days. Refresh tokens rotate
  on every use. The database stores only SHA-256 hashes of the tokens.
- **Local listening only.** The server listens on `127.0.0.1`. The tunnel is the only way in, so
  no port is opened on your router.

Use a long random passphrase: anyone who has it can read your training data and put workouts on
your calendar. Set `SELFCOACH_ALLOW_WRITE=0` for a read-only server.

## 1. Install on the server

Any Linux machine with Python 3.12 (uv installs it). Commands for Debian/Ubuntu:

```bash
# dedicated system user
sudo useradd --system --create-home --home-dir /var/lib/selfcoach --shell /usr/sbin/nologin selfcoach
sudo install -d -o selfcoach -g selfcoach -m 750 /opt/selfcoach /var/lib/selfcoach/data

# uv + code
sudo -u selfcoach bash -c 'curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh'
sudo -u selfcoach bash -c 'cd /opt/selfcoach && git clone https://github.com/danielerpl/selfcoach-mcp.git repo \
  && cd repo && ~/.local/bin/uv sync --no-dev'
```

**Garmin login.** Do it on the server:

```bash
sudo -u selfcoach env SELFCOACH_HOME=/var/lib/selfcoach/data /opt/selfcoach/repo/.venv/bin/selfcoach-mcp login
sudo -u selfcoach env SELFCOACH_HOME=/var/lib/selfcoach/data /opt/selfcoach/repo/.venv/bin/selfcoach-mcp sync --days 180
```

If you already use selfcoach locally, you can copy `~/.selfcoach/tokens` and `garmin.db` to
`/var/lib/selfcoach/data/` instead. That keeps your notes and goals. In that case, **stop using
the local copy** and point your local clients at the remote server too (step 4). Two copies of the
same Garmin tokens that refresh independently can invalidate each other.

**Settings.** Create `/etc/selfcoach/selfcoach.env` (owner `root:selfcoach`, mode `640`) from
[`deploy/selfcoach.env.example`](../deploy/selfcoach.env.example):

```bash
SELFCOACH_HOME=/var/lib/selfcoach/data
SELFCOACH_PUBLIC_URL=https://selfcoach.example.com
SELFCOACH_PASSPHRASE=<long random secret>
SELFCOACH_LANGUAGE=Italian          # optional
```

Set the machine's time zone to yours (`timedatectl set-timezone Europe/Rome`). "Today" is computed
with the server's clock.

## 2. Run it as a service

```bash
sudo cp /opt/selfcoach/repo/deploy/selfcoach.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now selfcoach
curl -s 127.0.0.1:8765/health            # {"status":"ok"}
curl -s -o /dev/null -w '%{http_code}\n' -X POST 127.0.0.1:8765/mcp   # 401 = auth is on
```

You can also start it by hand with `selfcoach-mcp serve --http --host 127.0.0.1 --port 8765`. It
refuses to start without an `https://` public URL and a passphrase of at least 12 characters.

## 3. Publish it with Cloudflare Tunnel

You need a domain on Cloudflare (the free plan is enough). The tunnel connects outwards from your
server, so nothing is opened on your router and Cloudflare provides HTTPS.

1. Install `cloudflared` on the server
   ([packages](https://pkg.cloudflare.com/index.html)).
2. In the Cloudflare dashboard go to **Zero Trust → Networks → Tunnels → Create a tunnel**
   (*Cloudflared*), name it `selfcoach` and copy the install command
   (`cloudflared service install <token>`). Run it on the server.
3. In the tunnel, add a **public hostname**, e.g. `selfcoach.example.com`, with service
   `http://127.0.0.1:8765`.
4. Check from anywhere:
   ```bash
   curl https://selfcoach.example.com/health
   curl https://selfcoach.example.com/.well-known/oauth-authorization-server
   ```

Do **not** put Cloudflare Access in front of it. claude.ai connects from Anthropic's servers and
can't pass an Access login. The server's own OAuth is the protection.

Other reverse proxies work as well (Caddy, nginx, Tailscale Funnel), as long as clients reach the
server over HTTPS at `SELFCOACH_PUBLIC_URL` and the proxy passes the `Host` header through.

## 4. Connect your clients

The MCP endpoint is **`https://<your-host>/mcp`**.

**claude.ai, the desktop app and the mobile apps.** In claude.ai go to **Settings → Connectors →
Add custom connector**, enter the name `selfcoach` and the URL `https://<your-host>/mcp`, then
choose *Connect*. Your login page opens: enter the passphrase. The connector then appears in every
Claude app signed in to your account, phones included. Custom connectors depend on your plan and
on your organisation's settings.

**Claude Code:**

```bash
claude mcp add --transport http selfcoach -s user https://<your-host>/mcp
```

Then in a new session run `/mcp` → *selfcoach* → *Authenticate* and enter the passphrase in the
browser.

**Other clients.** Any client that supports remote MCP servers with OAuth works.

## 5. Maintenance

- **Update:** `sudo -u selfcoach git -C /opt/selfcoach/repo pull && sudo -u selfcoach
  /var/lib/selfcoach/.local/bin/uv --directory /opt/selfcoach/repo sync --no-dev && sudo systemctl restart selfcoach`
- **Logs:** `journalctl -u selfcoach -f` and `journalctl -u cloudflared -f`.
- **Garmin status:** `sudo -u selfcoach env $(grep -v PASSPHRASE /etc/selfcoach/selfcoach.env | xargs) /opt/selfcoach/repo/.venv/bin/selfcoach-mcp check`
- **Disconnect every client:** change `SELFCOACH_PASSPHRASE`, delete the rows in the `oauth_tokens`
  table and restart. Clients must then log in again.
- **Backups:** `/var/lib/selfcoach/data` holds everything (Garmin tokens and database).

## Troubleshooting

| Symptom | Fix |
|---|---|
| claude.ai: "couldn't connect" | `curl https://<host>/.well-known/oauth-authorization-server` must return JSON. If not, check the tunnel and the service. |
| `421 Misdirected Request` / "Invalid Host header" in the logs | `SELFCOACH_PUBLIC_URL` doesn't match the hostname clients use. |
| Registration refused (`invalid_redirect_uri`) | The client uses a redirect host that isn't allowed. Add it to `SELFCOACH_OAUTH_REDIRECT_HOSTS`. |
| "Too many wrong attempts" | Wait 15 minutes. |
| Tools answer with "could not sync with Garmin" | Run `selfcoach-mcp check` on the server. If the tokens expired, run `login` again there. |
