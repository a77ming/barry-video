#!/usr/bin/env node

import { spawn } from "node:child_process";
import http from "node:http";
import path from "node:path";
import process from "node:process";
import { chmod, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { setTimeout as delay } from "node:timers/promises";

const API_BASE = "https://api-scenter.inbeidou.cn";
const DEFAULT_TIMEOUT_MS = 5 * 60 * 1000;
const POLL_INTERVAL_MS = 5 * 1000;
const OPENCLAW_HOME = expandHome(process.env.OPENCLAW_HOME || "~/.openclaw");
const OPENCLAW_CONFIG_FILE = path.join(OPENCLAW_HOME, "openclaw.json");
const AUTH_HOME = expandHome(process.env.BARRY_VIDEO_AUTH_HOME || "~/.barry-video");
const AUTH_FILE = path.join(AUTH_HOME, "auth.json");
const PLUGIN_ID = "barry-video";

function expandHome(value) {
  if (!value) {
    return value;
  }
  if (value === "~") {
    return process.env.HOME || value;
  }
  if (value.startsWith("~/")) {
    return path.join(process.env.HOME || "", value.slice(2));
  }
  return value;
}

function printUsage(stderr = false) {
  const stream = stderr ? process.stderr : process.stdout;
  stream.write(
    [
      "Usage:",
      "  barry-video login [--no-open] [--timeout-ms 300000]",
      "  barry-video logout",
      "  barry-video status"
    ].join("\n") + "\n"
  );
}

function parseArgs(argv) {
  const parsed = {
    command: argv[0] || "help",
    noOpen: false,
    timeoutMs: DEFAULT_TIMEOUT_MS
  };

  for (let index = 1; index < argv.length; index += 1) {
    const value = argv[index];

    if (value === "--no-open") {
      parsed.noOpen = true;
      continue;
    }

    if (value === "--timeout-ms") {
      const nextValue = argv[index + 1];
      const timeoutMs = Number.parseInt(nextValue || "", 10);
      if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
        throw new Error("--timeout-ms requires a positive integer");
      }
      parsed.timeoutMs = timeoutMs;
      index += 1;
      continue;
    }

    throw new Error(`Unknown argument: ${value}`);
  }

  return parsed;
}

async function readJson(filePath) {
  try {
    const text = await readFile(filePath, "utf8");
    return JSON.parse(text);
  } catch (error) {
    if (error && error.code === "ENOENT") {
      return {};
    }
    if (error instanceof SyntaxError) {
      throw new Error(`Invalid JSON file: ${filePath}`);
    }
    throw error;
  }
}

async function writeJson(filePath, data, secure = false) {
  await mkdir(path.dirname(filePath), { recursive: true });
  await writeFile(filePath, `${JSON.stringify(data, null, 2)}\n`, "utf8");
  if (secure) {
    await chmod(filePath, 0o600).catch(() => {});
  }
}

function ensurePluginConfig(config) {
  const root = config && typeof config === "object" ? config : {};
  root.plugins = root.plugins && typeof root.plugins === "object" ? root.plugins : {};
  root.plugins.entries = root.plugins.entries && typeof root.plugins.entries === "object" ? root.plugins.entries : {};
  root.plugins.entries[PLUGIN_ID] =
    root.plugins.entries[PLUGIN_ID] && typeof root.plugins.entries[PLUGIN_ID] === "object"
      ? root.plugins.entries[PLUGIN_ID]
      : {};
  root.plugins.entries[PLUGIN_ID].config =
    root.plugins.entries[PLUGIN_ID].config && typeof root.plugins.entries[PLUGIN_ID] === "object"
      ? root.plugins.entries[PLUGIN_ID].config
      : {};
  return root;
}

function getPluginAuthToken(config) {
  return config?.plugins?.entries?.[PLUGIN_ID]?.config?.authToken || "";
}

async function syncStoredTokens() {
  const authCache = await readJson(AUTH_FILE);
  const openclawConfig = ensurePluginConfig(await readJson(OPENCLAW_CONFIG_FILE));
  const authFileToken = authCache.authToken || "";
  const openclawToken = getPluginAuthToken(openclawConfig);
  const token = authFileToken || openclawToken;

  if (!token) {
    return {
      token: "",
      authFileToken: "",
      openclawToken: ""
    };
  }

  if (authFileToken !== token) {
    await writeJson(
      AUTH_FILE,
      {
        authToken: token,
        updatedAt: authCache.updatedAt || new Date().toISOString(),
        ...(authCache.source ? { source: authCache.source } : {}),
        ...(authCache.userInfo ? { userInfo: authCache.userInfo } : {})
      },
      true
    );
  }

  if (openclawToken !== token) {
    openclawConfig.plugins.entries[PLUGIN_ID].config.authToken = token;
    await writeJson(OPENCLAW_CONFIG_FILE, openclawConfig);
  }

  return {
    token,
    authFileToken: authFileToken || token,
    openclawToken: openclawToken || token
  };
}

async function persistToken(token, metadata = {}) {
  const now = new Date().toISOString();

  const authPayload = {
    authToken: token,
    updatedAt: now,
    ...metadata
  };
  await writeJson(AUTH_FILE, authPayload, true);

  const openclawConfig = ensurePluginConfig(await readJson(OPENCLAW_CONFIG_FILE));
  openclawConfig.plugins.entries[PLUGIN_ID].config.authToken = token;
  await writeJson(OPENCLAW_CONFIG_FILE, openclawConfig);
}

async function clearPersistedToken() {
  await rm(AUTH_FILE, { force: true }).catch(() => {});

  const openclawConfig = await readJson(OPENCLAW_CONFIG_FILE);
  const pluginConfig = openclawConfig?.plugins?.entries?.[PLUGIN_ID]?.config;
  if (pluginConfig && Object.prototype.hasOwnProperty.call(pluginConfig, "authToken")) {
    delete pluginConfig.authToken;
    await writeJson(OPENCLAW_CONFIG_FILE, openclawConfig);
  }
}

async function apiPost(apiPath, payload) {
  let response;
  try {
    response = await fetch(`${API_BASE}${apiPath}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    });
  } catch (error) {
    throw new Error(`Request failed: ${error.message}`);
  }

  const text = await response.text();
  let json;

  try {
    json = JSON.parse(text);
  } catch {
    const excerpt = text.trim().slice(0, 200);
    throw new Error(`API returned non-JSON response: HTTP ${response.status}${excerpt ? `, body=${excerpt}` : ""}`);
  }

  json.httpStatus = response.status;
  return json;
}

function requireSuccess(payload, action) {
  if (!payload || payload.code !== 0) {
    throw new Error(`${action} failed: ${payload?.msg || `HTTP ${payload?.httpStatus || "unknown"}`}`);
  }
  return payload.body || {};
}

function updateStatus(state, phase, message) {
  state.phase = phase;
  state.message = message;
  state.updatedAt = new Date().toISOString();
}

function renderLoginPage(qrByte) {
  const qrDataUrl = `data:image/jpeg;base64,${qrByte}`;

  return `<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Barry Video Login</title>
    <style>
      :root {
        --bg: #f5efe3;
        --paper: #fcf8f0;
        --ink: #162113;
        --muted: #627060;
        --line: #d8cfbf;
        --accent: #2d7f55;
        --accent-soft: #dcecdf;
        --warn: #9c6b24;
        --warn-soft: #f6ead2;
        --err: #a02f2f;
        --err-soft: #f7dddd;
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        min-height: 100vh;
        background:
          radial-gradient(circle at 0 0, rgba(45,127,85,0.12), transparent 28%),
          radial-gradient(circle at 100% 100%, rgba(189,142,54,0.12), transparent 24%),
          var(--bg);
        color: var(--ink);
        font-family: "PingFang SC", "Hiragino Sans GB", "Noto Sans SC", sans-serif;
        display: grid;
        place-items: center;
        padding: 24px;
      }
      .card {
        width: min(880px, 100%);
        background: rgba(252, 248, 240, 0.94);
        border: 1px solid var(--line);
        border-radius: 28px;
        box-shadow: 0 28px 80px rgba(40, 32, 16, 0.12);
        overflow: hidden;
      }
      .layout {
        display: grid;
        grid-template-columns: 1.15fr 0.85fr;
      }
      .panel {
        padding: 34px;
      }
      .hero {
        background:
          linear-gradient(180deg, rgba(45,127,85,0.08), rgba(45,127,85,0)),
          var(--paper);
        border-right: 1px solid var(--line);
      }
      .eyebrow {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 8px 12px;
        border-radius: 999px;
        background: var(--accent-soft);
        color: var(--accent);
        font-size: 13px;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
      }
      h1 {
        margin: 18px 0 10px;
        font-size: clamp(30px, 4vw, 52px);
        line-height: 0.98;
      }
      p {
        margin: 0;
        color: var(--muted);
        font-size: 15px;
        line-height: 1.7;
      }
      .steps {
        margin-top: 28px;
        display: grid;
        gap: 12px;
      }
      .step {
        display: grid;
        grid-template-columns: 34px 1fr;
        gap: 12px;
        align-items: start;
        padding: 14px 16px;
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.6);
        border: 1px solid rgba(216, 207, 191, 0.8);
      }
      .step-num {
        width: 34px;
        height: 34px;
        border-radius: 50%;
        display: grid;
        place-items: center;
        background: var(--ink);
        color: white;
        font-weight: 700;
      }
      .qr-wrap {
        display: grid;
        gap: 18px;
        justify-items: center;
        align-content: center;
        min-height: 100%;
      }
      .qr-box {
        width: min(320px, 100%);
        aspect-ratio: 1;
        background: white;
        border-radius: 24px;
        padding: 20px;
        border: 1px solid var(--line);
        box-shadow: 0 18px 48px rgba(26, 32, 17, 0.08);
      }
      .qr-box img {
        display: block;
        width: 100%;
        height: 100%;
        object-fit: contain;
      }
      .status {
        width: 100%;
        border-radius: 18px;
        padding: 16px 18px;
        background: var(--warn-soft);
        color: var(--warn);
        border: 1px solid rgba(156, 107, 36, 0.18);
      }
      .status[data-phase="success"] {
        background: var(--accent-soft);
        color: var(--accent);
        border-color: rgba(45, 127, 85, 0.18);
      }
      .status[data-phase="error"],
      .status[data-phase="expired"] {
        background: var(--err-soft);
        color: var(--err);
        border-color: rgba(160, 47, 47, 0.18);
      }
      .status strong {
        display: block;
        font-size: 14px;
        margin-bottom: 6px;
      }
      .hint {
        font-size: 13px;
        text-align: center;
        color: var(--muted);
      }
      @media (max-width: 820px) {
        .layout {
          grid-template-columns: 1fr;
        }
        .hero {
          border-right: 0;
          border-bottom: 1px solid var(--line);
        }
      }
    </style>
  </head>
  <body>
    <div class="card">
      <div class="layout">
        <section class="panel hero">
          <div class="eyebrow">Barry Video Auth</div>
          <h1>微信扫码<br />直接登录</h1>
          <p>这个二维码来自 Inbeidou 官方登录接口。扫码成功后，Barry Video 会自动保存 token 到本地配置，不需要再手填 cookie 或手抄 token。</p>
          <div class="steps">
            <div class="step">
              <div class="step-num">1</div>
              <div>用微信扫描右侧二维码。</div>
            </div>
            <div class="step">
              <div class="step-num">2</div>
              <div>在微信里确认登录。</div>
            </div>
            <div class="step">
              <div class="step-num">3</div>
              <div>页面出现“授权成功”后，关闭这个标签页即可。</div>
            </div>
          </div>
        </section>
        <section class="panel">
          <div class="qr-wrap">
            <div class="qr-box">
              <img src="${qrDataUrl}" alt="Barry Video login QR code" />
            </div>
            <div class="status" id="status" data-phase="pending">
              <strong>等待扫码</strong>
              <span id="message">等待微信扫码登录</span>
            </div>
            <div class="hint" id="updatedAt">状态会自动刷新</div>
          </div>
        </section>
      </div>
    </div>
    <script>
      const phaseLabel = {
        pending: "等待扫码",
        success: "授权成功",
        expired: "二维码已过期",
        error: "登录失败"
      };

      async function refreshStatus() {
        try {
          const response = await fetch("/status", { cache: "no-store" });
          const status = await response.json();
          const phase = status.phase || "pending";
          document.getElementById("status").dataset.phase = phase;
          document.getElementById("status").querySelector("strong").textContent = phaseLabel[phase] || phase;
          document.getElementById("message").textContent = status.message || "";
          document.getElementById("updatedAt").textContent = status.updatedAt
            ? "最后更新 " + new Date(status.updatedAt).toLocaleTimeString()
            : "状态会自动刷新";
          if (phase === "success" || phase === "expired" || phase === "error") {
            clearInterval(timer);
          }
        } catch (error) {
          document.getElementById("status").dataset.phase = "error";
          document.getElementById("status").querySelector("strong").textContent = "状态获取失败";
          document.getElementById("message").textContent = error.message || "无法获取状态";
          clearInterval(timer);
        }
      }

      const timer = setInterval(refreshStatus, 2000);
      refreshStatus();
    </script>
  </body>
</html>`;
}

async function startStatusServer(qrByte, state) {
  const server = http.createServer((request, response) => {
    const pathname = request.url || "/";

    if (pathname === "/status") {
      response.writeHead(200, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
      response.end(JSON.stringify(state));
      return;
    }

    if (pathname === "/favicon.ico") {
      response.writeHead(204);
      response.end();
      return;
    }

    response.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" });
    response.end(renderLoginPage(qrByte));
  });

  await new Promise((resolve) => {
    server.listen(0, "127.0.0.1", resolve);
  });

  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;

  return {
    server,
    url: `http://127.0.0.1:${port}/`
  };
}

function openBrowser(url) {
  let command = "";
  let args = [];

  if (process.platform === "darwin") {
    command = "open";
    args = [url];
  } else if (process.platform === "win32") {
    command = "cmd";
    args = ["/c", "start", "", url];
  } else {
    command = "xdg-open";
    args = [url];
  }

  try {
    const child = spawn(command, args, {
      detached: true,
      stdio: "ignore"
    });
    child.unref();
    return true;
  } catch {
    return false;
  }
}

async function loginWithQr(options) {
  const loginBody = requireSuccess(
    await apiPost("/agent/v1/user/login/create_qr", {
      env_version: "release",
      page: "package-other/pages/jump/index",
      check_path: false,
      scene: "i="
    }),
    "Create login QR"
  );

  if (!loginBody.qrByte || !loginBody.si) {
    throw new Error("Login QR response was missing qrByte or si");
  }

  const state = {
    phase: "pending",
    message: "等待微信扫码登录",
    updatedAt: new Date().toISOString()
  };
  const { server, url } = await startStatusServer(loginBody.qrByte, state);
  let browserOpened = false;

  try {
    if (!options.noOpen) {
      browserOpened = openBrowser(url);
    }

    process.stdout.write(`Barry Video login page: ${url}\n`);
    if (options.noOpen) {
      process.stdout.write("Browser auto-open skipped.\n");
    } else if (!browserOpened) {
      process.stdout.write("Browser did not open automatically. Open the URL above manually.\n");
    }
    process.stdout.write("Waiting for WeChat QR confirmation...\n");

    const deadline = Date.now() + options.timeoutMs;

    while (Date.now() < deadline) {
      const payload = await apiPost("/agent/v1/user/login/check_qr", { si: loginBody.si });

      if (payload.code === 0 && payload.body?.access_token) {
        await persistToken(payload.body.access_token, {
          source: "wechat-qr",
          userInfo: payload.body.user_info || null
        });
        updateStatus(state, "success", "授权成功，token 已写入本地配置");
        process.stdout.write(`Login succeeded. Saved token to ${AUTH_FILE} and ${OPENCLAW_CONFIG_FILE}\n`);
        await delay(1500);
        return 0;
      }

      if (payload.code === 10406) {
        updateStatus(state, "pending", payload.msg || "扫码等待登录中");
        const remainingMs = deadline - Date.now();
        if (remainingMs <= 0) {
          break;
        }
        await delay(Math.min(POLL_INTERVAL_MS, remainingMs));
        continue;
      }

      updateStatus(state, "error", payload.msg || "二维码登录失败");
      throw new Error(payload.msg || "二维码登录失败");
    }

    updateStatus(state, "expired", "二维码已超时，请重新运行 barry-video login");
    throw new Error("二维码已超时，请重新运行 barry-video login");
  } finally {
    await new Promise((resolve) => {
      server.close(resolve);
    });
  }
}

async function showStatus() {
  const authCache = await readJson(AUTH_FILE);
  const { token, authFileToken, openclawToken } = await syncStoredTokens();

  if (!token) {
    process.stdout.write("No saved Barry Video auth token was found.\n");
    process.stdout.write(`Checked: ${AUTH_FILE}\n`);
    process.stdout.write(`Checked: ${OPENCLAW_CONFIG_FILE}\n`);
    return 1;
  }

  process.stdout.write(`Auth file: ${AUTH_FILE}\n`);
  process.stdout.write(`OpenClaw config: ${OPENCLAW_CONFIG_FILE}\n`);
  process.stdout.write(`Auth file token: ${authFileToken ? "present" : "missing"}\n`);
  process.stdout.write(`OpenClaw token: ${openclawToken ? "present" : "missing"}\n`);
  if (authCache.updatedAt) {
    process.stdout.write(`Last updated: ${authCache.updatedAt}\n`);
  }
  return 0;
}

async function logout() {
  await clearPersistedToken();
  process.stdout.write(`Cleared Barry Video auth from ${AUTH_FILE} and ${OPENCLAW_CONFIG_FILE}\n`);
  return 0;
}

async function main() {
  let args;

  try {
    args = parseArgs(process.argv.slice(2));
  } catch (error) {
    printUsage(true);
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
    return;
  }

  try {
    if (args.command === "login") {
      process.exitCode = await loginWithQr(args);
      return;
    }

    if (args.command === "logout") {
      process.exitCode = await logout();
      return;
    }

    if (args.command === "status") {
      process.exitCode = await showStatus();
      return;
    }

    printUsage(args.command !== "help");
    process.exitCode = args.command === "help" ? 0 : 1;
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}

await main();
