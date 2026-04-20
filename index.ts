import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PLUGIN_ID = "barry-video";
const PLUGIN_NAME = "Barry Video";
const PLUGIN_ROOT = path.dirname(fileURLToPath(import.meta.url));
const PRIVATE_BACKEND = path.join(PLUGIN_ROOT, "backend", "inbeidou_cli.py");
const PLATFORMS = ["TIKTOK", "FACEBOOK", "INSTAGRAM", "YOUTUBE"];
const CUT_TYPES = ["high_cut", "high_mixed", "golden_three", "golden_clips", "high_pre"];
const DEDUP_OPTIONS = [
  "common_deduplication",
  "apply_pip",
  "apply_rotate",
  "apply_scale",
  "apply_flip",
  "apply_frame",
  "apply_special",
  "apply_speed",
  "apply_reduce_frame_rate",
  "apply_mirror_pip"
];
const CONFIG_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {
    pythonBin: {
      type: "string",
      default: "python3",
      description: "Python executable used to run the Inbeidou backend CLI."
    },
    backendCli: {
      type: "string",
      default: "~/inbeidou_cli.py",
      description: "Absolute path to the existing inbeidou_cli.py backend script."
    },
    authToken: {
      type: "string",
      default: "",
      description: "Optional Inbeidou token passed to the backend as INBEIDOU_TOKEN."
    },
    downloadDir: {
      type: "string",
      default: "~/Desktop",
      description: "Default output directory for downloaded clipped or translated videos."
    },
    defaultAccountIds: {
      type: "array",
      items: { type: "string" },
      default: [],
      description: "Default publish account IDs used when no accountIds are provided."
    },
    defaultTeamIds: {
      type: "array",
      items: { type: "string" },
      default: [],
      description: "Default publish team IDs used when no teamIds are provided."
    },
    defaultPublishPlatform: {
      type: "string",
      enum: PLATFORMS,
      default: "FACEBOOK",
      description: "Default social platform when publishing by team IDs."
    },
    defaultDramaPlatform: {
      type: "string",
      default: "dramabox",
      description: "Default short drama platform when users ask for latest dramas."
    },
    defaultLanguage: {
      type: "string",
      default: "2",
      description: "Default language ID for short drama listing."
    },
    defaultDramaOrder: {
      type: "string",
      default: "publish_at",
      description: "Default sort field for short drama listing."
    }
  }
};

function expandHome(value) {
  if (typeof value !== "string" || value.length === 0) {
    return value;
  }
  if (value === "~") {
    return os.homedir();
  }
  if (value.startsWith("~/")) {
    return path.join(os.homedir(), value.slice(2));
  }
  return value;
}

function normalizeList(value) {
  if (value === undefined || value === null || value === "") {
    return [];
  }
  if (Array.isArray(value)) {
    return value.flatMap((item) => normalizeList(item));
  }
  if (typeof value === "string") {
    return value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  }
  return [String(value)];
}

function getPluginConfig(api) {
  const entries = api?.config?.plugins?.entries || {};
  const entry = entries[PLUGIN_ID] || {};
  const config = entry.config || {};
  return config && typeof config === "object" ? config : {};
}

function getRuntimeDefaults(api) {
  const config = getPluginConfig(api);
  return {
    defaultAccountIds: normalizeList(config.defaultAccountIds),
    defaultTeamIds: normalizeList(config.defaultTeamIds),
    defaultPublishPlatform: config.defaultPublishPlatform || "FACEBOOK",
    defaultDramaPlatform: config.defaultDramaPlatform || "dramabox",
    defaultLanguage: String(config.defaultLanguage || "2"),
    defaultDramaOrder: config.defaultDramaOrder || "publish_at"
  };
}

function resolvePythonBin(api) {
  const config = getPluginConfig(api);
  return config.pythonBin || process.env.BARRY_VIDEO_PYTHON || "python3";
}

function readBeidouAuthToken() {
  const authStatePath = path.join(os.homedir(), ".barry-video", "auth_state.json");
  try {
    const raw = readFileSync(authStatePath, "utf8");
    const state = JSON.parse(raw);
    if (
      state.access_token &&
      state.status === "success" &&
      state.expired_at &&
      state.expired_at > Date.now()
    ) {
      return state.access_token;
    }
  } catch {
    // no valid beidou auth state
  }
  return "";
}

function resolveAuthToken(api) {
  const config = getPluginConfig(api);
  return (
    config.authToken ||
    process.env.BARRY_VIDEO_AUTH_TOKEN ||
    process.env.BARRY_VIDEO_TOKEN ||
    process.env.INBEIDOU_TOKEN ||
    readBeidouAuthToken() ||
    ""
  );
}

function resolveBackendCli(api) {
  const config = getPluginConfig(api);
  const candidates = [
    expandHome(process.env.BARRY_VIDEO_BACKEND || ""),
    expandHome(config.backendCli || ""),
    PRIVATE_BACKEND,
    path.join(os.homedir(), "inbeidou_cli.py"),
    "/Users/ming/inbeidou_cli.py"
  ].filter(Boolean);

  for (const candidate of candidates) {
    if (existsSync(candidate)) {
      return candidate;
    }
  }
  return candidates[0] || "";
}

function resolveDownloadDir(api, overrideDir) {
  const config = getPluginConfig(api);
  return expandHome(overrideDir || config.downloadDir || path.join(os.homedir(), "Desktop"));
}

function addOption(args, flag, value) {
  if (value !== undefined && value !== null && value !== "") {
    args.push(flag, String(value));
  }
}

function addFlag(args, enabled, flag) {
  if (enabled) {
    args.push(flag);
  }
}

function addRepeatedOptions(args, flag, values) {
  for (const value of normalizeList(values)) {
    args.push(flag, String(value));
  }
}

function maybeParseJson(text) {
  const raw = String(text || "").trim();
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function requireAny(params, keys, message) {
  const found = keys.some((key) => params[key] !== undefined && params[key] !== null && params[key] !== "");
  if (!found) {
    throw new Error(message);
  }
}

function requireNonEmptyList(values, message) {
  if (normalizeList(values).length === 0) {
    throw new Error(message);
  }
}

function toolResponse(title, payload, command) {
  const parts = [title];
  if (command) {
    parts.push(`command: ${command}`);
  }
  if (payload !== undefined && payload !== null) {
    if (typeof payload === "string") {
      parts.push(payload.trim());
    } else {
      parts.push(JSON.stringify(payload, null, 2));
    }
  }
  return {
    content: [
      {
        type: "text",
        text: parts.filter(Boolean).join("\n\n")
      }
    ]
  };
}

async function runBackend(api, cliArgs, options = {}) {
  const pythonBin = resolvePythonBin(api);
  const backendCli = resolveBackendCli(api);
  const authToken = resolveAuthToken(api);

  if (!backendCli) {
    throw new Error("Barry Video backend is not configured. Set plugins.entries['barry-video'].config.backendCli.");
  }
  if (!existsSync(backendCli)) {
    throw new Error(`Barry Video backend does not exist: ${backendCli}`);
  }

  const commandArgs = [backendCli, ...cliArgs];
  const command = [pythonBin, ...commandArgs].join(" ");

  return await new Promise((resolve, reject) => {
    const child = spawn(pythonBin, commandArgs, {
      env: {
        ...process.env,
        ...(authToken ? { INBEIDOU_TOKEN: authToken } : {})
      },
      stdio: ["ignore", "pipe", "pipe"]
    });

    let stdout = "";
    let stderr = "";
    let settled = false;
    let timer = null;

    if (options.timeoutMs && Number(options.timeoutMs) > 0) {
      timer = setTimeout(() => {
        child.kill("SIGTERM");
      }, Number(options.timeoutMs));
    }

    child.stdout.on("data", (chunk) => {
      stdout += String(chunk);
    });

    child.stderr.on("data", (chunk) => {
      stderr += String(chunk);
    });

    child.on("error", (error) => {
      if (timer) {
        clearTimeout(timer);
      }
      if (!settled) {
        settled = true;
        reject(error);
      }
    });

    child.on("close", (code) => {
      if (timer) {
        clearTimeout(timer);
      }
      if (settled) {
        return;
      }
      settled = true;
      if (code !== 0) {
        reject(new Error(`Command failed (${code}): ${command}\n${stderr || stdout}`.trim()));
        return;
      }
      resolve({
        command,
        stdout: stdout.trim(),
        stderr: stderr.trim()
      });
    });
  });
}

async function runJsonTool(api, title, cliArgs, options = {}) {
  const result = await runBackend(api, cliArgs, options);
  const payload = maybeParseJson(result.stdout);
  return toolResponse(title, payload ?? result.stdout, result.command);
}

function buildClipArgs(params) {
  requireAny(params, ["file", "uploadId"], "clip requires file or uploadId");
  const args = ["clip", "create"];
  addOption(args, "--file", params.file);
  addOption(args, "--upload-id", params.uploadId);
  addOption(args, "--window-id", params.windowId);
  addOption(args, "--cut-type", params.cutType);
  addOption(args, "--duration", params.duration);
  addOption(args, "--output-count", params.outputCount);
  addOption(args, "--script-count", params.scriptCount);
  for (const value of normalizeList(params.deduplication)) {
    args.push("--deduplication", value);
  }
  addOption(args, "--watermark", params.watermark);
  addFlag(args, params.mergeVideo, "--merge-video");
  addFlag(args, params.wait !== false, "--wait");
  addOption(args, "--upload-timeout", params.uploadTimeout);
  addOption(args, "--submit-timeout", params.submitTimeout);
  addOption(args, "--timeout", params.timeout);
  addOption(args, "--poll-interval", params.pollInterval);
  args.push("--json");
  return args;
}

function buildTranslateArgs(params) {
  requireAny(params, ["file", "uploadId"], "translate requires file or uploadId");
  if (!params.targetLang) {
    throw new Error("translate requires targetLang");
  }
  const args = ["translate", "create"];
  addOption(args, "--file", params.file);
  addOption(args, "--upload-id", params.uploadId);
  addOption(args, "--window-id", params.windowId);
  addOption(args, "--source-lang", params.sourceLang);
  addOption(args, "--lang", params.targetLang);
  addOption(args, "--subtitle-type", params.subtitleType);
  addFlag(args, params.noSpeechTranslate, "--no-speech-translate");
  addOption(args, "--font", params.font);
  addOption(args, "--font-size", params.fontSize);
  addOption(args, "--font-color", params.fontColor);
  addOption(args, "--font-opacity", params.fontOpacity);
  addOption(args, "--subtitle-y", params.subtitleY);
  addOption(args, "--alignment", params.alignment);
  addOption(args, "--effect-style", params.effectStyle);
  addFlag(args, params.bold, "--bold");
  addFlag(args, params.underline, "--underline");
  addFlag(args, params.italic, "--italic");
  addFlag(args, params.shadow, "--shadow");
  addOption(args, "--shadow-shift", params.shadowShift);
  addOption(args, "--shadow-x-bord", params.shadowXBord);
  addOption(args, "--shadow-y-bord", params.shadowYBord);
  addOption(args, "--shadow-opacity", params.shadowOpacity);
  addFlag(args, params.outline, "--outline");
  addOption(args, "--outline-board", params.outlineBoard);
  addFlag(args, params.mergeVideo, "--merge-video");
  addFlag(args, params.wait !== false, "--wait");
  addOption(args, "--upload-timeout", params.uploadTimeout);
  addOption(args, "--submit-timeout", params.submitTimeout);
  addOption(args, "--timeout", params.timeout);
  addOption(args, "--poll-interval", params.pollInterval);
  args.push("--json");
  return args;
}

function resolvePublishTargets(api, params) {
  const defaults = getRuntimeDefaults(api);
  const accountIds = normalizeList(params.accountIds);
  const teamIds = normalizeList(params.teamIds);
  if (accountIds.length > 0 || teamIds.length > 0) {
    return { accountIds, teamIds };
  }
  return {
    accountIds: defaults.defaultAccountIds,
    teamIds: defaults.defaultTeamIds
  };
}

function buildPublishArgs(api, params) {
  const defaults = getRuntimeDefaults(api);
  const { accountIds, teamIds } = resolvePublishTargets(api, params);
  if (accountIds.length === 0 && teamIds.length === 0) {
    throw new Error("publish requires accountIds or teamIds, or plugin defaults.");
  }

  const args = ["publish", "create", "--json"];
  addRepeatedOptions(args, "--account-id", accountIds);
  addRepeatedOptions(args, "--team-id", teamIds);
  addOption(args, "--platform", params.platform || (teamIds.length > 0 ? defaults.defaultPublishPlatform : ""));
  addOption(args, "--text", params.text);
  addOption(args, "--text-file", params.textFile);
  addOption(args, "--file", params.file);
  addOption(args, "--file-url", params.fileUrl);
  addOption(args, "--schedule-at", params.scheduleAt);
  addFlag(args, params.dryRun, "--dry-run");
  return args;
}

function buildDramaArgs(api, params = {}) {
  const defaults = getRuntimeDefaults(api);
  const args = ["list"];
  addOption(args, "--platform", params.platform || defaults.defaultDramaPlatform);
  addOption(args, "--language", params.language || defaults.defaultLanguage);
  addOption(args, "--search", params.search);
  addOption(args, "--page", params.page);
  addOption(args, "--size", params.size);
  addOption(args, "--order", params.order || defaults.defaultDramaOrder);
  args.push("--json");
  return args;
}

function buildUploadListArgs(params = {}) {
  const args = ["uploads", "list", "--json"];
  addOption(args, "--page", params.page);
  addOption(args, "--size", params.size);
  return args;
}

function buildManusListArgs(params = {}) {
  const args = ["manus", "list", "--json"];
  addOption(args, "--page", params.page);
  addOption(args, "--size", params.size);
  addOption(args, "--search", params.search);
  return args;
}

function registerJsonTool(api, definition, argsBuilder) {
  api.registerTool({
    ...definition,
    async execute(_id, params = {}) {
      return await runJsonTool(api, definition.description, argsBuilder(params));
    }
  });
}

function registerBarryTools(api) {
  registerJsonTool(
    api,
    {
      name: "barry_video_user",
      description: "Get the current Inbeidou account profile.",
      parameters: { type: "object", additionalProperties: false, properties: {} }
    },
    () => ["user", "--json"]
  );

  registerJsonTool(
    api,
    {
      name: "barry_video_credit",
      description: "Get the current Inbeidou credit balance.",
      parameters: { type: "object", additionalProperties: false, properties: {} }
    },
    () => ["credit", "--json"]
  );

  registerJsonTool(
    api,
    {
      name: "barry_video_products",
      description: "List Inbeidou AI products and prices.",
      parameters: { type: "object", additionalProperties: false, properties: {} }
    },
    () => ["products", "--json"]
  );

  registerJsonTool(
    api,
    {
      name: "barry_video_languages",
      description: "List supported Inbeidou translation language catalogs.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          type: { type: "string", enum: ["all", "speech", "target", "subtitle"] }
        }
      }
    },
    (params) => {
      const args = ["languages", "--json"];
      addOption(args, "--type", params.type);
      return args;
    }
  );

  registerJsonTool(
    api,
    {
      name: "barry_video_dramas",
      description: "List short dramas from Dramabox, ShortMax, or other supported platforms.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          platform: { type: "string" },
          language: { type: "string" },
          search: { type: "string" },
          page: { type: "integer" },
          size: { type: "integer" },
          order: { type: "string" }
        }
      }
    },
    (params) => buildDramaArgs(api, params)
  );

  registerJsonTool(
    api,
    {
      name: "barry_video_publish_accounts",
      description: "List authorized social publish accounts for Facebook, Instagram, TikTok, or YouTube.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          platform: { type: "string", enum: PLATFORMS },
          status: { type: "integer", enum: [0, 1, 2] }
        }
      }
    },
    (params) => {
      const args = ["publish", "accounts", "--json"];
      addOption(args, "--platform", params.platform);
      addOption(args, "--status", params.status);
      return args;
    }
  );

  registerJsonTool(
    api,
    {
      name: "barry_video_uploads_list",
      description: "List videos in the Inbeidou media library.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          page: { type: "integer" },
          size: { type: "integer" }
        }
      }
    },
    (params) => buildUploadListArgs(params)
  );

  registerJsonTool(
    api,
    {
      name: "barry_video_upload",
      description: "Upload a local video into the Inbeidou media library.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          file: { type: "string" },
          uploadTimeout: { type: "integer" },
          pollInterval: { type: "number" }
        },
        required: ["file"]
      }
    },
    (params) => {
      const args = ["uploads", "upload", "--file", params.file, "--json"];
      addOption(args, "--upload-timeout", params.uploadTimeout);
      addOption(args, "--poll-interval", params.pollInterval);
      return args;
    }
  );

  registerJsonTool(
    api,
    {
      name: "barry_video_uploads_delete",
      description: "Delete a video from the Inbeidou media library.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          fileId: { type: "string" }
        },
        required: ["fileId"]
      }
    },
    (params) => ["uploads", "delete", "--id", params.fileId, "--json"]
  );

  registerJsonTool(
    api,
    {
      name: "barry_video_analyze",
      description: "Run Inbeidou smart video analysis on a local file or uploaded asset.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          file: { type: "string" },
          uploadId: { type: "string" },
          windowId: { type: "string" },
          timeout: { type: "integer" },
          pollInterval: { type: "number" }
        }
      }
    },
    (params) => {
      requireAny(params, ["file", "uploadId"], "analyze requires file or uploadId");
      const args = ["analyze", "run", "--json"];
      addOption(args, "--file", params.file);
      addOption(args, "--upload-id", params.uploadId);
      addOption(args, "--window-id", params.windowId);
      addOption(args, "--timeout", params.timeout);
      addOption(args, "--poll-interval", params.pollInterval);
      return args;
    }
  );

  registerJsonTool(
    api,
    {
      name: "barry_video_clip_types",
      description: "List supported smart clip types.",
      parameters: { type: "object", additionalProperties: false, properties: {} }
    },
    () => ["clip", "types", "--json"]
  );

  registerJsonTool(
    api,
    {
      name: "barry_video_clip",
      description: "Run Inbeidou smart clipping on a local file or uploaded asset.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          file: { type: "string" },
          uploadId: { type: "string" },
          windowId: { type: "string" },
          cutType: { type: "string", enum: CUT_TYPES },
          duration: { type: "string" },
          outputCount: { type: "integer" },
          scriptCount: { type: "integer" },
          deduplication: {
            type: "array",
            items: { type: "string", enum: DEDUP_OPTIONS }
          },
          watermark: { type: "string" },
          mergeVideo: { type: "boolean" },
          wait: { type: "boolean" },
          uploadTimeout: { type: "integer" },
          submitTimeout: { type: "integer" },
          timeout: { type: "integer" },
          pollInterval: { type: "number" }
        }
      }
    },
    (params) => buildClipArgs(params)
  );

  registerJsonTool(
    api,
    {
      name: "barry_video_translate_languages",
      description: "List supported translation languages for the translate workflow.",
      parameters: { type: "object", additionalProperties: false, properties: {} }
    },
    () => ["translate", "languages", "--json"]
  );

  registerJsonTool(
    api,
    {
      name: "barry_video_translate_fonts",
      description: "List supported subtitle fonts for the translate workflow.",
      parameters: { type: "object", additionalProperties: false, properties: {} }
    },
    () => ["translate", "fonts", "--json"]
  );

  registerJsonTool(
    api,
    {
      name: "barry_video_translate_styles",
      description: "List supported subtitle effect styles for the translate workflow.",
      parameters: { type: "object", additionalProperties: false, properties: {} }
    },
    () => ["translate", "styles", "--json"]
  );

  registerJsonTool(
    api,
    {
      name: "barry_video_translate",
      description: "Run Inbeidou video translation on a local file or uploaded asset.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          file: { type: "string" },
          uploadId: { type: "string" },
          windowId: { type: "string" },
          sourceLang: { type: "string" },
          targetLang: { type: "string" },
          subtitleType: { type: "string", enum: ["double", "single"] },
          noSpeechTranslate: { type: "boolean" },
          font: { type: "string" },
          fontSize: { type: "integer" },
          fontColor: { type: "string" },
          fontOpacity: { type: "integer" },
          subtitleY: { type: "number" },
          alignment: { type: "string", enum: ["Left", "Center", "Right"] },
          effectStyle: { type: "string" },
          bold: { type: "boolean" },
          underline: { type: "boolean" },
          italic: { type: "boolean" },
          shadow: { type: "boolean" },
          shadowShift: { type: "number" },
          shadowXBord: { type: "number" },
          shadowYBord: { type: "number" },
          shadowOpacity: { type: "integer" },
          outline: { type: "boolean" },
          outlineBoard: { type: "number" },
          mergeVideo: { type: "boolean" },
          wait: { type: "boolean" },
          uploadTimeout: { type: "integer" },
          submitTimeout: { type: "integer" },
          timeout: { type: "integer" },
          pollInterval: { type: "number" }
        },
        required: ["targetLang"]
      }
    },
    (params) => buildTranslateArgs(params)
  );

  registerJsonTool(
    api,
    {
      name: "barry_video_manus_list",
      description: "List generated works in the Inbeidou manus library.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          page: { type: "integer" },
          size: { type: "integer" },
          search: { type: "string" }
        }
      }
    },
    (params) => buildManusListArgs(params)
  );

  registerJsonTool(
    api,
    {
      name: "barry_video_manus_detail",
      description: "Get details for a generated Inbeidou work.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          manusId: { type: "string" }
        },
        required: ["manusId"]
      }
    },
    (params) => ["manus", "detail", "--id", params.manusId, "--json"]
  );

  registerJsonTool(
    api,
    {
      name: "barry_video_download_manus",
      description: "Download a completed generated video to a local directory.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          manusId: { type: "string" },
          outputDir: { type: "string" }
        },
        required: ["manusId"]
      }
    },
    (params) => ["manus", "download", "--id", params.manusId, "--output", resolveDownloadDir(api, params.outputDir), "--json"]
  );

  registerJsonTool(
    api,
    {
      name: "barry_video_manus_delete",
      description: "Delete a generated work from the manus library.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          manusId: { type: "string" }
        },
        required: ["manusId"]
      }
    },
    (params) => ["manus", "delete", "--id", params.manusId, "--json"]
  );

  registerJsonTool(
    api,
    {
      name: "barry_video_publish",
      description: "Create a social publish task for Facebook, Instagram, TikTok, or YouTube.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          accountIds: {
            type: "array",
            items: { type: "string" }
          },
          teamIds: {
            type: "array",
            items: { type: "string" }
          },
          platform: { type: "string", enum: PLATFORMS },
          text: { type: "string" },
          textFile: { type: "string" },
          file: { type: "string" },
          fileUrl: { type: "string" },
          scheduleAt: { type: "string" },
          dryRun: { type: "boolean" }
        }
      }
    },
    (params) => {
      requireAny(params, ["file", "fileUrl"], "publish requires file or fileUrl");
      return buildPublishArgs(api, params);
    }
  );

  registerJsonTool(
    api,
    {
      name: "barry_video_publish_records",
      description: "List social publish task records and statuses.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          postStatus: { type: "string", enum: ["published", "scheduled"] },
          platform: { type: "string", enum: PLATFORMS },
          socialId: { type: "string" },
          status: { type: "string" },
          page: { type: "integer" },
          size: { type: "integer" }
        }
      }
    },
    (params) => {
      const args = ["publish", "records", "--json"];
      addOption(args, "--post-status", params.postStatus);
      addOption(args, "--platform", params.platform);
      addOption(args, "--social-id", params.socialId);
      addOption(args, "--status", params.status);
      addOption(args, "--page", params.page);
      addOption(args, "--size", params.size);
      return args;
    }
  );

  registerJsonTool(
    api,
    {
      name: "barry_video_publish_delete",
      description: "Delete a publish record or scheduled task.",
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          teamId: { type: "string" },
          taskId: { type: "string" },
          postId: { type: "string" }
        },
        required: ["teamId", "taskId"]
      }
    },
    (params) => {
      const args = ["publish", "delete", "--team-id", params.teamId, "--task-id", params.taskId, "--json"];
      addOption(args, "--post-id", params.postId);
      return args;
    }
  );

  api.registerTool({
    name: "barry_video_pipeline",
    description: "Run a one-shot workflow: smart clip, download the result, then publish it to a social account.",
    parameters: {
      type: "object",
      additionalProperties: false,
      properties: {
        file: { type: "string" },
        accountIds: {
          type: "array",
          items: { type: "string" }
        },
        teamIds: {
          type: "array",
          items: { type: "string" }
        },
        platform: { type: "string", enum: PLATFORMS },
        text: { type: "string" },
        textFile: { type: "string" },
        scheduleAt: { type: "string" },
        downloadDir: { type: "string" },
        cutType: { type: "string", enum: CUT_TYPES },
        duration: { type: "string" },
        outputCount: { type: "integer" },
        scriptCount: { type: "integer" },
        deduplication: {
          type: "array",
          items: { type: "string", enum: DEDUP_OPTIONS }
        },
        watermark: { type: "string" },
        mergeVideo: { type: "boolean" },
        uploadTimeout: { type: "integer" },
        submitTimeout: { type: "integer" },
        timeout: { type: "integer" },
        pollInterval: { type: "number" }
      },
      required: ["file"]
    },
    async execute(_id, params) {
      const clipResult = await runBackend(api, buildClipArgs({ ...params, wait: true }));
      const clipBody = maybeParseJson(clipResult.stdout);
      const manusId = clipBody?.id || clipBody?.manus_id || clipBody?.manusId;

      if (!manusId) {
        throw new Error(`clip result did not include manus id: ${clipResult.stdout}`);
      }

      const downloadArgs = ["manus", "download", "--id", String(manusId), "--output", resolveDownloadDir(api, params.downloadDir), "--json"];
      const downloadResult = await runBackend(api, downloadArgs);
      const downloadBody = maybeParseJson(downloadResult.stdout);
      const downloadedFile = downloadBody?.path;

      if (!downloadedFile) {
        throw new Error(`download result did not include path: ${downloadResult.stdout}`);
      }

      const publishArgs = buildPublishArgs(api, { ...params, file: downloadedFile });
      const publishResult = await runBackend(api, publishArgs);
      const publishBody = maybeParseJson(publishResult.stdout) ?? publishResult.stdout;

      return toolResponse(
        "Run a one-shot workflow: smart clip, download the result, then publish it to a social account.",
        {
          clip: clipBody,
          download: downloadBody,
          publish: publishBody
        },
        `${clipResult.command}\n${downloadResult.command}\n${publishResult.command}`
      );
    }
  });

  api.registerTool({
    name: "barry_video_cli_passthrough",
    description: "Run raw inbeidou_cli.py arguments when no dedicated Barry Video tool exists.",
    parameters: {
      type: "object",
      additionalProperties: false,
      properties: {
        args: {
          type: "array",
          items: { type: "string" }
        }
      },
      required: ["args"]
    },
    async execute(_id, params) {
      requireNonEmptyList(params.args, "args is required");
      const result = await runBackend(api, normalizeList(params.args));
      return toolResponse("Run raw inbeidou_cli.py arguments when no dedicated Barry Video tool exists.", maybeParseJson(result.stdout) ?? result.stdout, result.command);
    }
  });
}

const plugin = {
  id: PLUGIN_ID,
  name: PLUGIN_NAME,
  description: "Barry's all-in-one Inbeidou creator plugin for account, drama, media, AI editing, and social publishing workflows.",
  configSchema: CONFIG_SCHEMA,
  register(api) {
    registerBarryTools(api);
  }
};

export default plugin;
