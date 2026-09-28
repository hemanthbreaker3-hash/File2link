import http from 'http';
import https from 'https';

export default async function handler(req, res) {
  const backendUrl = process.env.BACKEND_URL;

  if (!backendUrl) {
    res.status(500).json({
      error: "BACKEND_URL environment variable is not configured.",
      message: "Please set BACKEND_URL in Vercel project environment variables (e.g. http://your-vps-ip:8000 or https://stream.yourdomain.com)."
    });
    return;
  }

  const cleanBackendUrl = backendUrl.replace(/\/+$/, '');
  const requestPath = req.url || '/';

  // For high-bandwidth streaming/download/remux routes, issue an HTTP 307 redirect directly to VPS
  if (
    requestPath.startsWith('/stream/') ||
    requestPath.startsWith('/dl/') ||
    requestPath.startsWith('/remux/')
  ) {
    res.writeHead(307, {
      Location: `${cleanBackendUrl}${requestPath}`
    });
    res.end();
    return;
  }

  // For HTML watch page, landing page, static assets, and API requests, proxy request to VPS
  const targetUrl = new URL(`${cleanBackendUrl}${requestPath}`);
  const transport = targetUrl.protocol === 'https:' ? https : http;

  const clientReq = transport.request(
    targetUrl.toString(),
    {
      method: req.method,
      headers: {
        ...req.headers,
        host: targetUrl.host
      }
    },
    (backendRes) => {
      res.writeHead(backendRes.statusCode, backendRes.headers);
      backendRes.pipe(res, { end: true });
    }
  );

  clientReq.on('error', (err) => {
    console.error('Error proxying request to backend VPS:', err);
    res.status(502).json({
      error: "Bad Gateway",
      message: "Could not connect to the backend VPS server. Please verify BACKEND_URL is accessible and running."
    });
  });

  if (['POST', 'PUT', 'PATCH'].includes(req.method)) {
    req.pipe(clientReq, { end: true });
  } else {
    clientReq.end();
  }
}
