# VM deployment — Twitter stalker only

Deploy JulyBot to a Linux VM with `BOT_MODE=twitter` (only `/stalk`, `/unstalk`, `/stalklist`, `/settwitterchannel`).

## Prerequisites on the VM (Oracle Cloud / etc.)

1. **Open inbound ports** in your cloud security list / firewall:
   - **TCP 22** — SSH (required to deploy)
   - **TCP 8080** — webhook server (twitterapi.io must reach this), *or* **443** if you put Caddy/nginx in front

2. **SSH access** — default user is often `ubuntu` (Ubuntu) or `opc` (Oracle Linux). Test from your PC:
   ```bash
   ssh -i ssh-key-2026-06-10.key ubuntu@40.233.86.65
   ```

3. **HTTPS for webhooks** — twitterapi.io expects a **public HTTPS** webhook URL. Options:
   - Point a domain at `40.233.86.65` and use Caddy + Let's Encrypt
   - Temporary dev: run `ngrok http 8080` *on the VM* and paste the HTTPS URL into the twitterapi.io dashboard

## Deploy from your PC (once SSH works)

```powershell
cd C:\Projects\JulyBot
.\deploy\deploy_to_vm.ps1
```

Or manually copy the repo to `/opt/julybot` and run:

```bash
chmod +x deploy/vm_setup.sh
./deploy/vm_setup.sh
```

## Configure on the VM

```bash
sudo nano /opt/julybot/.env
```

Use [deploy/env.twitter.example](env.twitter.example) as a template. Required values:

| Variable | Where to get it |
|----------|-----------------|
| `DISCORD_TOKEN` | [Discord Developer Portal](https://discord.com/developers/applications) → Bot → Token |
| `DISCORD_GUILD_ID` | Discord → Server Settings → Widget → Server ID |
| `TWITTERAPI_IO_KEY` | [twitterapi.io dashboard](https://twitterapi.io/dashboard) |

Start the service:

```bash
sudo systemctl start julybot-twitter
sudo journalctl -u julybot-twitter -f
```

## Register Discord bot (you have not done this yet)

1. Go to https://discord.com/developers/applications → **New Application**
2. **Bot** tab → **Reset Token** → copy into `DISCORD_TOKEN`
3. Enable **Message Content Intent** if prompted (optional for slash commands)
4. **OAuth2 → URL Generator** — scopes: `bot`, `applications.commands`; permissions: Send Messages, Embed Links
5. Open the generated invite URL and add the bot to your server
6. Copy **Server ID** into `DISCORD_GUILD_ID` (Developer Mode on in Discord → right-click server → Copy Server ID)

## Register twitterapi.io webhook

1. Dashboard → set webhook URL to e.g. `https://YOUR_DOMAIN/webhooks/twitter` or ngrok URL
2. In Discord: `/settwitterchannel #alerts` then `/stalk username`

## Troubleshooting SSH timeout

If `ssh` times out to `40.233.86.65`:

- VM is stopped in Oracle Cloud console → start it
- Security list missing ingress rule for **0.0.0.0/0 TCP 22**
- Wrong username (`ubuntu` vs `opc`)
- Instance uses a **private** IP only — use the public IP from the console
