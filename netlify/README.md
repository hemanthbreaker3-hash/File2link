# 🌐 Deploying Base URL on Netlify

This setup allows you to deploy your frontend / Base URL on **Netlify** while keeping the Telegram Bot and high-speed media streaming engine hosted on your **VPS** (via `./deploy.vps`).

---

## 🛠️ Step-by-Step Instructions

### Step 1: Deploy Bot & Backend on VPS
Deploy the main application on your VPS using `deploy.vps`:

```bash
chmod +x deploy.vps
./deploy.vps
```

---

### Step 2: Deploy to Netlify
1. Log into [Netlify](https://app.netlify.com/).
2. Click **Add new site -> Import an existing project**.
3. Connect your GitHub repository.
4. Netlify will automatically detect `netlify.toml`.

---

### Step 3: Configure Environment Variable
1. In your Netlify site settings, navigate to **Site configuration -> Environment variables**.
2. Add environment variable:
   - **Key:** `BACKEND_URL`
   - **Value:** Your VPS backend address (e.g. `http://YOUR_VPS_IP:8000` or `https://stream.yourdomain.com`).
3. Trigger a redeploy to apply changes.

---

### Step 4: Update Base URL in Telegram Bot
In your Telegram Bot, run:

```text
/baseurl https://your-site-name.netlify.app
```

All generated links will now use your Netlify URL! 🎉
