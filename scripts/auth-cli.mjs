#!/usr/bin/env node

import path from "node:path";
import process from "node:process";
import { chmod, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { setTimeout as delay } from "node:timers/promises";

const require = createRequire(import.meta.url);
const jpeg = require("jpeg-js");

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

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function luminance(r, g, b) {
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function decodeQrImage(qrByte) {
  try {
    return jpeg.decode(Buffer.from(qrByte, "base64"), {
      useTArray: true
    });
  } catch (error) {
    throw new Error(`Failed to decode QR image: ${error.message}`);
  }
}

function isContentPixel(r, g, b, a = 255) {
  if (a < 8) {
    return false;
  }

  const spread = Math.max(r, g, b) - Math.min(r, g, b);
  return luminance(r, g, b) < 246 || spread > 10;
}

function findContentBounds(image) {
  const { data, width, height } = image;
  let minX = width;
  let minY = height;
  let maxX = -1;
  let maxY = -1;

  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const index = (y * width + x) * 4;
      const r = data[index];
      const g = data[index + 1];
      const b = data[index + 2];
      const a = data[index + 3];

      if (!isContentPixel(r, g, b, a)) {
        continue;
      }

      minX = Math.min(minX, x);
      minY = Math.min(minY, y);
      maxX = Math.max(maxX, x);
      maxY = Math.max(maxY, y);
    }
  }

  if (maxX < 0 || maxY < 0) {
    return {
      left: 0,
      top: 0,
      right: width - 1,
      bottom: height - 1
    };
  }

  const padding = Math.max(10, Math.round(Math.min(width, height) * 0.04));
  return {
    left: clamp(minX - padding, 0, width - 1),
    top: clamp(minY - padding, 0, height - 1),
    right: clamp(maxX + padding, 0, width - 1),
    bottom: clamp(maxY + padding, 0, height - 1)
  };
}

function calculateOtsuThreshold(image, bounds) {
  const histogram = new Uint32Array(256);
  const { data, width } = image;
  let total = 0;
  let sum = 0;

  for (let y = bounds.top; y <= bounds.bottom; y += 1) {
    for (let x = bounds.left; x <= bounds.right; x += 1) {
      const index = (y * width + x) * 4;
      const level = Math.round(luminance(data[index], data[index + 1], data[index + 2]));
      histogram[level] += 1;
      total += 1;
      sum += level;
    }
  }

  let backgroundWeight = 0;
  let backgroundSum = 0;
  let bestVariance = -1;
  let threshold = 180;

  for (let level = 0; level < histogram.length; level += 1) {
    backgroundWeight += histogram[level];
    if (backgroundWeight === 0) {
      continue;
    }

    const foregroundWeight = total - backgroundWeight;
    if (foregroundWeight === 0) {
      break;
    }

    backgroundSum += level * histogram[level];
    const backgroundMean = backgroundSum / backgroundWeight;
    const foregroundMean = (sum - backgroundSum) / foregroundWeight;
    const variance = backgroundWeight * foregroundWeight * (backgroundMean - foregroundMean) ** 2;

    if (variance > bestVariance) {
      bestVariance = variance;
      threshold = level;
    }
  }

  return threshold;
}

function getTerminalQrSize() {
  const configured = Number.parseInt(process.env.BARRY_VIDEO_QR_SIZE || "", 10);
  if (Number.isFinite(configured) && configured >= 48) {
    return configured % 2 === 0 ? configured : configured - 1;
  }

  const fallbackColumns = process.stdout.isTTY ? 96 : 72;
  const maxSize = process.stdout.isTTY ? 96 : 72;
  const columns = process.stdout.columns || fallbackColumns;
  const size = Math.max(48, Math.min(maxSize, columns - 4));
  return size % 2 === 0 ? size : size - 1;
}

function downsampleQrImage(image, bounds, targetSize) {
  const { data, width } = image;
  const sourceWidth = bounds.right - bounds.left + 1;
  const sourceHeight = bounds.bottom - bounds.top + 1;
  const threshold = Math.min(236, calculateOtsuThreshold(image, bounds) + 12);
  const pixels = new Uint8Array(targetSize * targetSize);

  for (let targetY = 0; targetY < targetSize; targetY += 1) {
    const startY = Math.floor(bounds.top + (sourceHeight * targetY) / targetSize);
    const endY = Math.max(startY + 1, Math.ceil(bounds.top + (sourceHeight * (targetY + 1)) / targetSize));

    for (let targetX = 0; targetX < targetSize; targetX += 1) {
      const startX = Math.floor(bounds.left + (sourceWidth * targetX) / targetSize);
      const endX = Math.max(startX + 1, Math.ceil(bounds.left + (sourceWidth * (targetX + 1)) / targetSize));

      let luminanceSum = 0;
      let saturationSum = 0;
      let samples = 0;

      for (let y = startY; y < endY; y += 1) {
        for (let x = startX; x < endX; x += 1) {
          const index = (y * width + x) * 4;
          const r = data[index];
          const g = data[index + 1];
          const b = data[index + 2];
          luminanceSum += luminance(r, g, b);
          saturationSum += Math.max(r, g, b) - Math.min(r, g, b);
          samples += 1;
        }
      }

      const averageLuminance = luminanceSum / samples;
      const averageSaturation = saturationSum / samples;
      const isDark = averageLuminance <= threshold || (averageLuminance <= 235 && averageSaturation >= 40);
      pixels[targetY * targetSize + targetX] = isDark ? 1 : 0;
    }
  }

  return pixels;
}

function renderQrToTerminal(pixels, size) {
  const lines = [];

  for (let y = 0; y < size; y += 2) {
    let line = "";

    for (let x = 0; x < size; x += 1) {
      const top = pixels[y * size + x];
      const bottom = y + 1 < size ? pixels[(y + 1) * size + x] : 0;

      if (top && bottom) {
        line += "█";
      } else if (top) {
        line += "▀";
      } else if (bottom) {
        line += "▄";
      } else {
        line += " ";
      }
    }

    lines.push(line);
  }

  return lines.join("\n");
}

async function persistQrImage(qrByte) {
  const filePath = path.join(AUTH_HOME, "last-login-qr.jpg");
  await mkdir(AUTH_HOME, { recursive: true });
  await writeFile(filePath, Buffer.from(qrByte, "base64"));
  return filePath;
}

async function showTerminalQr(qrByte) {
  const image = decodeQrImage(qrByte);
  const bounds = findContentBounds(image);
  const size = getTerminalQrSize();
  const pixels = downsampleQrImage(image, bounds, size);
  const savedImage = await persistQrImage(qrByte);

  process.stdout.write("\n");
  process.stdout.write(renderQrToTerminal(pixels, size));
  process.stdout.write("\n\n");
  process.stdout.write("Use WeChat to scan the QR above.\n");
  process.stdout.write(`Raw QR image saved at: ${savedImage}\n`);
  process.stdout.write("If the QR is hard to scan, widen the terminal and rerun, or set BARRY_VIDEO_QR_SIZE=120.\n");
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
  let lastStatusMessage = "";

  await showTerminalQr(loginBody.qrByte);
  if (options.noOpen) {
    process.stdout.write("--no-open is kept for compatibility; login now uses terminal QR output.\n");
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
      return 0;
    }

    if (payload.code === 10406) {
      const pendingMessage = payload.msg || "扫码等待登录中";
      updateStatus(state, "pending", pendingMessage);
      if (pendingMessage !== lastStatusMessage) {
        process.stdout.write(`Status: ${pendingMessage}\n`);
        lastStatusMessage = pendingMessage;
      }
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
