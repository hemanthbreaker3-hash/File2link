# 🌐 Deploying Base URL on Vercel

This directory (`vercel/`) allows you to deploy your frontend / Base URL on **Vercel** while keeping the Telegram Bot and high-speed media streaming pipeline hosted on your **VPS** (via `./deploy.vps`).

---

## 🚀 How It Works

1. **VPS Deployment (`deploy.vps`):**
   - Runs Telegram Bot, FastAPI streaming engine, MongoDB, and Redis on your VPS (port `8000`).
2. **Vercel Base URL Deployment (`vercel/`):**
   - Serves as your custom `BASE_URL` (e.g. `https://your-app.vercel.app` or custom domain).
   - Proxies watch pages (`/watch/*`) and API calls directly to the VPS backend.
   - Redirects heavy media streams (`/stream/*`, `/dl/*`, `/remux/*`) with HTTP 307 to your VPS backend for maximum download speed and zero Vercel bandwidth timeout limits.

---

## 🛠️ Step-by-Step Deployment Instructions

### Step 1: Deploy Bot & Backend on VPS
Deploy the main application on your VPS using `deploy.vps`:

```bash
chmod +x deploy.vps
./deploy.vps
```

Ensure port `8000` (or your configured port) is accessible via public IP or domain (e.g. `http://YOUR_VPS_IP:8000` or Cloudflare Tunnel `https://vps.yourdomain.com`).

---

### Step 2: Deploy `vercel/` Directory to Vercel

#### Option A: Via Vercel CLI (Recommended)
1. Install Vercel CLI:
   ```bash
   npm install -g vercel
   ```
2. Navigate to the `vercel/` folder:
   ```bash
   cd vercel
   ```
3. Deploy to Vercel:
   ```bash
   vercel
   ```
4. Follow prompts to deploy.

#### Option B: Via GitHub Repository
1. Push your repository to GitHub.
2. Go to [Vercel Dashboard](https://vercel.com/new).
3. Import your project repository.
4. Set **Root Directory** to `vercel`.
5. Click **Deploy**.

---

### Step 3: Configure Environment Variables in Vercel
In your Vercel project settings:
1. Go to **Settings -> Environment Variables**.
2. Add a new variable:
   - **Key:** `BACKEND_URL`
   - **Value:** Your VPS Backend URL (e.g., `http://123.45.67.89:8000` or `https://vps-stream.yourdomain.com`).
3. Save and redeploy the Vercel project to apply the variable.

---

### Step 4: Update Base URL on Telegram Bot

In your Telegram Bot, send the admin command:

```text
/baseurl https://your-app.vercel.app
```

Or set `BASE_URL=https://your-app.vercel.app` in `.env` on your VPS and restart with `./deploy.vps`.

All generated download & watch links from the bot will now use your Vercel URL! 🎉
