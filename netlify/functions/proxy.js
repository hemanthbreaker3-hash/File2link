import http from 'http';
import https from 'https';

import fs from 'fs';
import path from 'path';

function getBackendUrl() {
  if (process.env.BACKEND_URL) {
    return process.env.BACKEND_URL;
  }
  try {
    const configPath = path.join(process.cwd(), 'netlify', 'config.json');
    if (fs.existsSync(configPath)) {
      const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
      if (config.BACKEND_URL && !config.BACKEND_URL.includes('YOUR_VPS_IP')) {
        return config.BACKEND_URL;
      }
    }
  } catch (e) {
    // Ignore config parse error
  }
  return null;
}

export async function handler(event, context) {
  const backendUrl = getBackendUrl();

  if (!backendUrl) {
    return {
      statusCode: 500,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        error: "BACKEND_URL environment variable is not configured.",
        message: "Please set BACKEND_URL in Netlify environment variables or netlify/config.json (e.g. http://your-vps-ip:8000 or https://stream.yourdomain.com)."
      })
    };
  }

  const cleanBackendUrl = backendUrl.replace(/\/+$/, '');
  const requestPath = event.path || '/';

  // For high-bandwidth streaming/download/remux routes, issue an HTTP 307 redirect directly to VPS
  if (
    requestPath.startsWith('/stream/') ||
    requestPath.startsWith('/dl/') ||
    requestPath.startsWith('/remux/')
  ) {
    return {
      statusCode: 307,
      headers: {
        Location: `${cleanBackendUrl}${requestPath}`
      },
      body: ''
    };
  }

  // Proxy requests for watch page, landing page, and API to VPS
  const queryString = event.rawQuery ? `?${event.rawQuery}` : '';
  const targetUrl = `${cleanBackendUrl}${requestPath}${queryString}`;

  return new Promise((resolve) => {
    const parsedUrl = new URL(targetUrl);
    const transport = parsedUrl.protocol === 'https:' ? https : http;

    const req = transport.request(
      targetUrl,
      {
        method: event.httpMethod,
        headers: {
          ...event.headers,
          host: parsedUrl.host
        }
      },
      (res) => {
        let body = [];
        res.on('data', (chunk) => body.push(chunk));
        res.on('end', () => {
          const buffer = Buffer.concat(body);
          const isText = (res.headers['content-type'] || '').includes('text') ||
                         (res.headers['content-type'] || '').includes('json') ||
                         (res.headers['content-type'] || '').includes('javascript');

          const responseHeaders = {};
          for (const [key, val] of Object.entries(res.headers)) {
            if (val) responseHeaders[key] = Array.isArray(val) ? val.join(', ') : val;
          }

          resolve({
            statusCode: res.statusCode,
            headers: responseHeaders,
            body: isText ? buffer.toString('utf-8') : buffer.toString('base64'),
            isBase64Encoded: !isText
          });
        });
      }
    );

    req.on('error', (err) => {
      console.error('Error proxying request to backend VPS:', err);
      resolve({
        statusCode: 502,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          error: "Bad Gateway",
          message: "Could not connect to the backend VPS server. Please verify BACKEND_URL is accessible and running."
        })
      });
    });

    if (event.body) {
      req.write(event.isBase64Encoded ? Buffer.from(event.body, 'base64') : event.body);
    }
    req.end();
  });
}
