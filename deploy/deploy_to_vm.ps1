# Deploy JulyBot Twitter-only to VM (bot + webhook; Supabase for DB).
param(
    [string]$VmIp = "40.233.86.65",
    [string]$SshUser = "opc",
    [string]$KeyPath = "ssh-key-2026-06-10.key",
    [switch]$SkipCleanup
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$key = Join-Path $root $KeyPath
$target = "${SshUser}@${VmIp}"
$sshArgs = @("-i", $key, "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=30")

icacls $key /inheritance:r | Out-Null
icacls $key /grant:r "$env:USERNAME`:R" | Out-Null

Write-Host "==> Testing SSH..."
& ssh @sshArgs $target "echo connected"

if (-not $SkipCleanup) {
    Write-Host "==> Cleaning previous install..."
    & scp -i $key (Join-Path $root "deploy\vm_cleanup.sh") "${target}:/tmp/vm_cleanup.sh"
    & ssh @sshArgs $target "sed -i 's/\r$//' /tmp/vm_cleanup.sh; bash /tmp/vm_cleanup.sh"
}

Write-Host "==> Syncing bot files only..."
& ssh @sshArgs $target "sudo mkdir -p /opt/julybot && sudo chown ${SshUser}:${SshUser} /opt/julybot"

$paths = @("config", "database", "discord_bot", "modules", "scripts", "deploy", "main.py", "requirements-twitter.txt")
foreach ($p in $paths) {
    $src = Join-Path $root $p
    if (Test-Path $src) {
        & scp -i $key -r $src "${target}:/opt/julybot/"
    }
}

Write-Host "==> Writing .env..."
$envContent = @"
BOT_MODE=twitter
DISCORD_TOKEN=REPLACE_ME
DISCORD_GUILD_ID=0
DATABASE_URL=REPLACE_WITH_SUPABASE_URL
TWITTERAPI_IO_KEY=new1_8d27814fe0844aaf860b41bd48f97ac6
TWITTER_WEBHOOK_HOST=0.0.0.0
TWITTER_WEBHOOK_PORT=8080
TWITTER_WEBHOOK_PATH=/webhooks/twitter
TWITTER_FILTER_INTERVAL_SECONDS=60
TWITTER_FILTER_TAG=julybot-stalk
COC_API_TOKEN=unused
"@
$envFile = Join-Path $env:TEMP "julybot.env"
Set-Content -Path $envFile -Value $envContent -NoNewline
& scp -i $key $envFile "${target}:/opt/julybot/.env"
Remove-Item $envFile

Write-Host "==> Running lightweight setup..."
& ssh @sshArgs $target "bash /opt/julybot/deploy/vm_setup.sh 2>&1 | tee /tmp/julybot-setup.log"

Write-Host ""
Write-Host "Done. Next on VM:"
Write-Host "  1. Edit /opt/julybot/.env (Supabase DATABASE_URL, DISCORD_TOKEN, DISCORD_GUILD_ID)"
Write-Host "  2. .venv/bin/python scripts/init_db.py"
Write-Host "  3. sudo systemctl start julybot-twitter"
