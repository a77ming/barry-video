#!/usr/bin/env node

import path from "node:path";
import process from "node:process";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";

const OPENCLAW_HOME = expandHome(process.env.OPENCLAW_HOME || "~/.openclaw");
const OPENCLAW_CONFIG_FILE = path.join(OPENCLAW_HOME, "openclaw.json");
const AUTH_HOME = expandHome(process.env.BARRY_VIDEO_AUTH_HOME || "~/.barry-video");
const AUTH_STATE_FILE = path.join(AUTH_HOME, "auth_state.json");
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
      "  barry-video logout",
      "  barry-video status"
    ].join("\n") + "\n"
  );
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

async function writeJson(filePath, data) {
  await mkdir(path.dirname(filePath), { recursive: true });
  await writeFile(filePath, `${JSON.stringify(data, null, 2)}\n`, "utf8");
}

async function showStatus() {
  const authState = await readJson(AUTH_STATE_FILE);
  const token = authState.access_token || "";

  if (!token) {
    process.stdout.write("No saved Barry Video auth token was found.\n");
    process.stdout.write(`Checked: ${AUTH_STATE_FILE}\n`);
    return 1;
  }

  const now = Date.now();
  const expiredAt = authState.expired_at;
  const expired = expiredAt ? expiredAt < now : false;

  process.stdout.write(`Auth file: ${AUTH_STATE_FILE}\n`);
  process.stdout.write(`Token: present\n`);
  process.stdout.write(`Status: ${authState.status || "unknown"}\n`);
  process.stdout.write(`Expired: ${expired}\n`);
  if (expiredAt) {
    process.stdout.write(`expired_at: ${expiredAt}\n`);
  }
  if (authState.agent_id) {
    process.stdout.write(`agent_id: ${authState.agent_id}\n`);
  }
  if (authState.authorize_time) {
    process.stdout.write(`authorize_time: ${authState.authorize_time}\n`);
  }
  if (authState.updated_at) {
    process.stdout.write(`updated_at: ${authState.updated_at}\n`);
  }
  return 0;
}

async function logout() {
  await rm(AUTH_STATE_FILE, { force: true }).catch(() => {});

  const openclawConfig = await readJson(OPENCLAW_CONFIG_FILE);
  const pluginConfig = openclawConfig?.plugins?.entries?.[PLUGIN_ID]?.config;
  if (pluginConfig && Object.prototype.hasOwnProperty.call(pluginConfig, "authToken")) {
    delete pluginConfig.authToken;
    await writeJson(OPENCLAW_CONFIG_FILE, openclawConfig);
  }

  process.stdout.write(`Cleared Barry Video auth from ${AUTH_STATE_FILE}\n`);
  return 0;
}

async function main() {
  const command = process.argv[2] || "help";

  try {
    if (command === "logout") {
      process.exitCode = await logout();
      return;
    }

    if (command === "status") {
      process.exitCode = await showStatus();
      return;
    }

    printUsage(command !== "help");
    process.exitCode = command === "help" ? 0 : 1;
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}

await main();
