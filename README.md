# 👑 Telegram Direct Media Streamer & Link Generator

> ⚡ **Ultra-Fast, Premium Telegram File to Direct Download & Streaming Link Generator Bot with Multi-Audio Track Switching, Web Player, and Cloudflare Tunnel Integration.**

---

## 🌟 Key Features

- ⚡ **Ultra-High Speed Streaming:** Powered by multi-session parallel downloads from Telegram servers.
- 🍿 **Web Media Player:** Instant browser playback with built-in Plyr player and support for external app players (VLC, MX Player, Infuse, PotPlayer, MPV, PLAYit, etc.).
- 🎧 **Multi-Audio Track Switcher:** Select and switch audio tracks on-the-fly using lossless FFmpeg remuxing.
- 👑 **Premium Bot UI:** Stylish Telegram UI with custom blockquotes (`>`), premium emojis, and interactive buttons.
- 🤖 **User-Only Protection:** Intelligent bot filtering ensuring the bot only responds to real human users.
- 🛡️ **Security & Admin Tools:** User ban/unban commands, rate limiting, channel force subscribe check, and broadcast features.
- 🚀 **One-Click VPS Deployment:** Fully automated single-command setup via `deploy.vps`.

---

## 💻 VPS System Requirements

| Resource | Minimum | Recommended |
| :--- | :--- | :--- |
| **OS** | Ubuntu 20.04/22.04/24.04, Debian 11/12, or AlmaLinux | Ubuntu 22.04 LTS |
| **CPU** | 1 vCPU | 2+ vCPU |
| **RAM** | 1 GB | 2 GB+ |
| **Disk Space** | 10 GB SSD | 20 GB+ SSD |
| **Docker** | Version 20.10+ | Latest |

---

## 🚀 Quick Start (One-Click VPS Deployment)

Deploying on a VPS takes only **one command**:

```bash
git clone https://github.com/your-username/your-repo.git
cd your-repo
chmod +x deploy.vps
./deploy.vps
```

### What `deploy.vps` Does Automatically:
1. Verifies root/sudo privileges and system requirements (`curl`, `git`).
2. Installs Docker and Docker Compose if not present.
3. Generates `.env` from `.env.example` if missing.
4. Validates configuration parameters.
5. Builds Docker images and starts MongoDB, Redis, and Web services.
6. Performs service health checks and displays live status summary.

---

## ⚙️ Configuration (`.env`)

Edit the `.env` file to configure your credentials:

```env
# Mandatory Telegram Credentials
API_ID=12345678
API_HASH=your_api_hash_here
BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyZ
OWNER_ID=123456789

# Administrative Settings
ADMINS=123456789,987654321
FORCE_SUB_CHANNELS=-1001234567890
CHANNEL_ID=-1001234567890

# Public Domain Base URL (HTTP/HTTPS)
BASE_URL=https://stream.yourdomain.com

# Multi-Session High-Speed Acceleration (Comma-Separated Telethon String Sessions)
SESSIONS=1BJW...AA=,1BJW...BB=

# Expiry & Port
DEFAULT_EXPIRY=0
PORT=8000
```

---

## ☁️ Setting Up Cloudflare Tunnel (Free HTTPS Domain)

Cloudflare Tunnel (`cloudflared`) connects your local web port (8000) directly to Cloudflare without opening incoming firewall ports or needing dynamic DNS.

### Step 1: Add Domain to Cloudflare
1. Sign up or log into [Cloudflare](https://www.cloudflare.com).
2. Add your domain name (e.g., `yourdomain.com`) and update your domain's nameservers at your registrar to Cloudflare's nameservers.

### Step 2: Install Cloudflared on VPS
Run the following commands on your VPS:

```bash
# Add Cloudflare GPG key and repository
sudo mkdir -p /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null

echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/cloudflared.list

# Install cloudflared
sudo apt-get update && sudo apt-get install -y cloudflared
```

### Step 3: Login to Cloudflare
Authenticate `cloudflared` with your Cloudflare account:

```bash
cloudflared tunnel login
```
*Click the URL displayed in the terminal to authorize the VPS.*

### Step 4: Create a Tunnel
Create a named tunnel (e.g., `tg-streamer`):

```bash
cloudflared tunnel create tg-streamer
```
*Note the Tunnel ID outputted by this command.*

### Step 5: Route Subdomain DNS
Route your subdomain to the tunnel:

```bash
cloudflared tunnel route dns tg-streamer stream.yourdomain.com
```

### Step 6: Create Tunnel Configuration
Create `/etc/cloudflared/config.yml` or `~/.cloudflared/config.yml`:

```yaml
tunnel: YOUR_TUNNEL_ID_HERE
credentials-file: /root/.cloudflared/YOUR_TUNNEL_ID_HERE.json

ingress:
  - hostname: stream.yourdomain.com
    service: http://localhost:8000
  - service: http_status:404
```

### Step 7: Install and Run Cloudflared Service
Install `cloudflared` as a system service so it starts automatically on boot:

```bash
sudo cloudflared --config /etc/cloudflared/config.yml service install
sudo systemctl start cloudflared
sudo systemctl enable cloudflared
```

### Step 8: Update BASE_URL and Restart Bot
Update `.env`:
```env
BASE_URL=https://stream.yourdomain.com
```

Redeploy:
```bash
./deploy.vps
```

---

## 🛠️ Service Management Commands

Manage your deployment using Docker Compose:

```bash
# View live application logs
docker compose logs -f web

# View all container statuses
docker compose ps

# Restart all services
docker compose restart

# Stop all services
docker compose down

# Rebuild and start services
docker compose up -d --build
```

---

## 🔄 Update & Redeploy Workflow

To update your deployment to the latest version:

```bash
git pull
./deploy.vps
```

---

## ❓ Troubleshooting & FAQs

### 1. Bot Is Not Responding To Messages
- Ensure `BOT_TOKEN`, `API_ID`, and `API_HASH` in `.env` are valid.
- Verify the bot user is an **Administrator** in any specified `FORCE_SUB_CHANNELS` or `CHANNEL_ID`.
- Check logs for errors: `docker compose logs -f web`.

### 2. MongoDB or Redis Connection Errors
- If running with Docker Compose, ensure `MONGODB_URI=mongodb://mongodb:27017/tg_media_bot` and `REDIS_URL=redis://redis:6379/0`.

### 3. Port 8000 Already In Use
- Stop conflicting services or change `PORT=8080` in `.env` and `docker-compose.yml`.

---

## 📄 License

This project is open-source under the MIT License.
