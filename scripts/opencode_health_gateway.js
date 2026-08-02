#!/usr/bin/env node
'use strict';

/*
 * Dependency-free health gateway for the containerized OpenCode web UI.
 *
 * The gateway owns the public listener on 0.0.0.0:${PORT:-3000} and spawns
 * `opencode web` on loopback with a fixed internal port as its real
 * upstream. Health probes (User-Agent prefix "kube-probe/" or
 * "ELB-HealthChecker/" on GET or HEAD of the exact root path "/") are
 * answered directly with a synthetic minimal HTTP 200 response
 * (Content-Type "text/plain; charset=utf-8", body "ok" for GET, empty
 * body for HEAD) without proxying upstream and without touching any
 * request header. All other traffic is proxied with the client's own
 * headers passed through untouched. Bodies are streamed, WebSocket
 * upgrades are forwarded, and child lifecycle and signals propagate.
 *
 * Credentials come from the environment only (required for the upstream
 * child); nothing is baked into the image, no credential value is ever
 * logged, and only Node.js built-in modules are used.
 */

const http = require('http');
const net = require('net');
const { spawn } = require('child_process');

const USERNAME_KEY = 'OPENCODE_SERVER_USERNAME';
const PASSWORD_KEY = 'OPENCODE_SERVER_PASSWORD';

const USERNAME = process.env[USERNAME_KEY] || '';
const PASSWORD = process.env[PASSWORD_KEY] || '';

// Fail closed: name only the missing key(s), never values, and exit
// non-zero before the public listener is created.
const missingKeys = [];
if (!USERNAME) missingKeys.push(USERNAME_KEY);
if (!PASSWORD) missingKeys.push(PASSWORD_KEY);
if (missingKeys.length > 0) {
  console.error(
    'Refusing to start: missing required environment variable(s): ' +
    missingKeys.join(', ')
  );
  process.exit(1);
}

const PUBLIC_PORT = Number(process.env.PORT) || 3000;
const PUBLIC_HOST = '0.0.0.0';

// Fixed internal loopback port for the real upstream (must differ from the
// public default port).
const INTERNAL_PORT = 13000;

// Credentials reach the child only through the inherited environment,
// never through argv.
const child = spawn(
  'opencode',
  ['web', '--hostname', '127.0.0.1', '--port', String(INTERNAL_PORT)],
  { stdio: 'inherit', cwd: '/workspace/teamflow' }
);

function isHealthProbe(req) {
  const userAgent = req.headers['user-agent'] || '';
  return (
    (req.method === 'GET' || req.method === 'HEAD') &&
    req.url === '/' &&
    (userAgent.startsWith('kube-probe/') ||
      userAgent.startsWith('ELB-HealthChecker/'))
  );
}

const server = http.createServer((req, res) => {
  if (isHealthProbe(req)) {
    // Answer health probes synthetically: minimal 200 response, no
    // upstream proxy call, no request-header modification.
    res.writeHead(200, { 'Content-Type': 'text/plain; charset=utf-8' });
    if (req.method === 'HEAD') {
      res.end();
    } else {
      res.end('ok');
    }
    return;
  }
  const headers = { ...req.headers };
  const proxyReq = http.request(
    {
      hostname: '127.0.0.1',
      port: INTERNAL_PORT,
      method: req.method,
      path: req.url,
      headers,
    },
    (proxyRes) => {
      res.writeHead(proxyRes.statusCode || 502, proxyRes.headers);
      proxyRes.pipe(res);
    }
  );
  proxyReq.on('error', () => {
    if (!res.headersSent) res.writeHead(502);
    res.end();
  });
  req.pipe(proxyReq);
});

// Forward WebSocket and other upgrade requests to the child over a raw
// TCP tunnel, preserving the client's headers.
server.on('upgrade', (req, socket, head) => {
  const upstream = net.connect(INTERNAL_PORT, '127.0.0.1', () => {
    upstream.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n`);
    for (const [name, value] of Object.entries(req.headers)) {
      if (Array.isArray(value)) {
        for (const entry of value) upstream.write(`${name}: ${entry}\r\n`);
      } else if (value !== undefined) {
        upstream.write(`${name}: ${value}\r\n`);
      }
    }
    upstream.write('\r\n');
    if (head && head.length > 0) upstream.write(head);
    upstream.pipe(socket).pipe(upstream);
  });
  upstream.on('error', () => socket.destroy());
  socket.on('error', () => upstream.destroy());
});

// Child lifecycle: if the real upstream dies, the gateway must not keep
// serving; exit non-zero so the orchestrator restarts the container.
child.on('error', (err) => {
  console.error(`Failed to spawn opencode web child: ${err.message}`);
  process.exit(1);
});

child.on('exit', (code, signal) => {
  console.error(
    `opencode web child exited (code=${code}, signal=${signal}); ` +
    'gateway exiting non-zero.'
  );
  process.exit(typeof code === 'number' && code !== 0 ? code : 1);
});

// Forward termination signals to the child, then exit.
function forwardSignal(signal) {
  process.on(signal, () => {
    child.kill(signal);
    process.exit(0);
  });
}
forwardSignal('SIGTERM');
forwardSignal('SIGINT');

server.listen(PUBLIC_PORT, PUBLIC_HOST, () => {
  console.log(
    `Health/auth gateway listening on ${PUBLIC_HOST}:${PUBLIC_PORT}; ` +
    `proxying to opencode web on 127.0.0.1:${INTERNAL_PORT}.`
  );
});
