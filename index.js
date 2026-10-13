#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const os = require("os");
const net = require("net");
const readline = require("readline");
const { spawn, execFile } = require("child_process");

// 1. Singleton Lock: Ensure only one daemon instance runs at any time
const SINGLETON_PIPE = process.platform === "win32"
  ? "\\\\.\\pipe\\antigravity-tokens-hud-lock"
  : path.join(os.tmpdir(), "antigravity-tokens-hud.sock");

const singletonServer = net.createServer();

singletonServer.once("error", (err) => {
  if (err.code === "EADDRINUSE") {
    console.log("[Antigravity Tokens HUD] Another daemon instance is already active. Exiting.");
    process.exit(0);
  } else {
    console.error("[Antigravity Tokens HUD] Singleton lock error:", err);
  }
});

if (process.platform !== "win32" && fs.existsSync(SINGLETON_PIPE)) {
  try { fs.unlinkSync(SINGLETON_PIPE); } catch (_) {}
}

singletonServer.listen(SINGLETON_PIPE, () => {
  singletonServer.unref(); // Do not block natural process exit
});

// Paths and constants
const TOKEN_STATS_SCRIPT = path.join(__dirname, "token_stats.py");
const CLIENT_SCRIPT_PATH = fs.existsSync(path.join(__dirname, "client_widget.js"))
  ? path.join(__dirname, "client_widget.js")
  : path.join(__dirname, "client_widget_injector.js");
const CONFIG_FILE = path.join(__dirname, "config.json");

function getDevToolsFilePath() {
  const home = os.homedir();
  if (process.platform === "darwin") {
    return path.join(home, "Library/Application Support/Antigravity/DevToolsActivePort");
  } else if (process.platform === "win32") {
    const appData = process.env.APPDATA || path.join(home, "AppData/Roaming");
    return path.join(appData, "Antigravity/DevToolsActivePort");
  } else {
    const configHome = process.env.XDG_CONFIG_HOME || path.join(home, ".config");
    const linuxPath = path.join(configHome, "Antigravity/DevToolsActivePort");
    if (fs.existsSync(linuxPath)) return linuxPath;

    // WSL: auto-detect Windows host Antigravity instance if running inside WSL
    const mntCUsers = "/mnt/c/Users";
    if (fs.existsSync(mntCUsers)) {
      try {
        const users = fs.readdirSync(mntCUsers);
        for (const u of users) {
          const wPath = path.join(mntCUsers, u, "AppData/Roaming/Antigravity/DevToolsActivePort");
          if (fs.existsSync(wPath)) return wPath;
        }
      } catch (_) {}
    }
    return linuxPath;
  }
}

let cdpMessageId = 1;
function nextCdpId() {
  cdpMessageId = (cdpMessageId + 1) & 0x7fffffff;
  return cdpMessageId;
}

// Map of pageId -> { ws, injected: boolean, pageId: string, url: string }
const activePages = new Map();
let currentPort = null;
let isUpdating = false;

// Cached client widget script
let cachedClientScript = "";
let cachedClientMtime = 0;
function getClientScript() {
  try {
    if (fs.existsSync(CLIENT_SCRIPT_PATH)) {
      const stat = fs.statSync(CLIENT_SCRIPT_PATH);
      if (stat.mtimeMs !== cachedClientMtime) {
        cachedClientScript = fs.readFileSync(CLIENT_SCRIPT_PATH, "utf8");
        cachedClientMtime = stat.mtimeMs;
      }
      return cachedClientScript;
    }
  } catch (_) {}
  return cachedClientScript;
}

function getConfigLang() {
  try {
    if (fs.existsSync(CONFIG_FILE)) {
      const cfg = JSON.parse(fs.readFileSync(CONFIG_FILE, "utf8"));
      if (cfg && cfg.lang) return cfg.lang;
    }
  } catch (_) {}
  return "en";
}

function getDevToolsInfo() {
  const devToolsFile = getDevToolsFilePath();
  if (!fs.existsSync(devToolsFile)) return null;
  try {
    const content = fs.readFileSync(devToolsFile, "utf8").trim().split("\n");
    if (content.length >= 1 && content[0]) {
      return { port: parseInt(content[0].trim(), 10) };
    }
  } catch (_) {}
  return null;
}

async function listAntigravityPages(port) {
  try {
    const res = await fetch(`http://127.0.0.1:${port}/json`, { signal: AbortSignal.timeout(1500) });
    const list = await res.json();
    return list.filter(p => p.type === "page" && p.url && (p.url.includes("127.0.0.1") || p.url.includes("localhost") || p.url.includes("antigravity")));
  } catch (_) {
    return [];
  }
}

// 2. Persistent Python Worker Manager
class PythonStatsWorker {
  constructor() {
    this.process = null;
    this.rl = null;
    this.pendingResolvers = [];
    this.isStarting = false;
    this.spawnWorker();
  }

  getPythonCommand() {
    const pythonBin = process.platform === "win32" ? "python" : "python3";
    const env = { ...process.env };
    if (process.platform !== "win32") {
      env.PATH = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", process.env.PATH || ""].join(":");
    }
    return { bin: pythonBin, env };
  }

  spawnWorker() {
    if (this.isStarting) return;
    this.isStarting = true;

    try {
      const { bin, env } = this.getPythonCommand();
      this.process = spawn(bin, [TOKEN_STATS_SCRIPT, "--worker"], {
        env,
        stdio: ["pipe", "pipe", "pipe"]
      });

      this.rl = readline.createInterface({
        input: this.process.stdout,
        crlfDelay: Infinity
      });

      this.rl.on("line", (line) => {
        if (!line.trim()) return;
        try {
          const res = JSON.parse(line);
          const cb = this.pendingResolvers.shift();
          if (cb) {
            if (res.status === "ok") {
              cb.resolve(res.data);
            } else {
              cb.resolve(null);
            }
          }
        } catch (_) {
          const cb = this.pendingResolvers.shift();
          if (cb) cb.resolve(null);
        }
      });

      this.process.stderr.on("data", () => {
        // Suppress stderr noise
      });

      this.process.on("error", () => {
        this.cleanup();
      });

      this.process.on("exit", () => {
        this.cleanup();
      });
    } catch (_) {
      this.cleanup();
    } finally {
      this.isStarting = false;
    }
  }

  cleanup() {
    while (this.pendingResolvers.length > 0) {
      const cb = this.pendingResolvers.shift();
      if (cb) cb.resolve(null);
    }
    try {
      if (this.rl) this.rl.close();
    } catch (_) {}
    try {
      if (this.process) this.process.kill();
    } catch (_) {}
    this.process = null;
    this.rl = null;
  }

  fetchStats(sessionId) {
    return new Promise((resolve) => {
      if (!this.process || this.process.killed) {
        this.spawnWorker();
      }

      if (!this.process || !this.process.stdin.writable) {
        // Fallback to one-shot execution if worker failed
        this.fallbackExec(sessionId, resolve);
        return;
      }

      const timer = setTimeout(() => {
        // Timeout safeguard: remove resolver and restart worker
        const idx = this.pendingResolvers.findIndex(cb => cb.timer === timer);
        if (idx !== -1) {
          this.pendingResolvers.splice(idx, 1);
          resolve(null);
          this.cleanup();
        }
      }, 3500);

      this.pendingResolvers.push({ resolve, timer });

      try {
        const payload = JSON.stringify({ action: "collect", session_id: sessionId }) + "\n";
        this.process.stdin.write(payload);
      } catch (_) {
        clearTimeout(timer);
        this.cleanup();
        this.fallbackExec(sessionId, resolve);
      }
    });
  }

  fallbackExec(sessionId, resolve) {
    const { bin, env } = this.getPythonCommand();
    const args = [TOKEN_STATS_SCRIPT, "--json"];
    if (sessionId) args.push("--session", sessionId);
    execFile(bin, args, { timeout: 6000, env }, (err, stdout) => {
      if (err || !stdout) {
        resolve(null);
        return;
      }
      try {
        resolve(JSON.parse(stdout));
      } catch (_) {
        resolve(null);
      }
    });
  }

  destroy() {
    this.cleanup();
  }
}

const statsWorker = new PythonStatsWorker();

function connectPageWebSocket(page) {
  return new Promise((resolve) => {
    let resolved = false;
    let ws = null;
    const timer = setTimeout(() => {
      if (!resolved) {
        resolved = true;
        try { if (ws) ws.close(); } catch (_) {}
        resolve(null);
      }
    }, 2000);

    try {
      ws = new WebSocket(page.webSocketDebuggerUrl);
      const entry = { ws, injected: false, pageId: page.id, url: page.url };

      ws.onopen = () => {
        if (!resolved) {
          resolved = true;
          clearTimeout(timer);
          activePages.set(page.id, entry);
          resolve(entry);
        }
      };

      ws.onmessage = (msg) => {
        try {
          const data = JSON.parse(msg.data);
          if (data.error) {
            const msgText = data.error.message || "";
            if (msgText.includes("execution context") || msgText.includes("detached") || msgText.includes("Target closed")) {
              entry.injected = false;
              try { ws.close(); } catch (_) {}
              activePages.delete(page.id);
            }
          } else if (data.result?.result?.value === "need_inject") {
            const script = getClientScript();
            if (script && ws.readyState === WebSocket.OPEN) {
              ws.send(JSON.stringify({
                id: nextCdpId(),
                method: "Runtime.evaluate",
                params: {
                  expression: `${script};\nwindow.__AGY_HUD_INSTALLED__ = true;`,
                  returnByValue: false
                }
              }));
              entry.injected = true;
            } else {
              entry.injected = false;
            }
          }
        } catch (_) {}
      };

      ws.onerror = () => {
        if (!resolved) {
          resolved = true;
          clearTimeout(timer);
          resolve(null);
        }
        activePages.delete(page.id);
      };

      ws.onclose = () => {
        activePages.delete(page.id);
      };
    } catch (_) {
      if (!resolved) {
        resolved = true;
        clearTimeout(timer);
        resolve(null);
      }
    }
  });
}

async function updateLoop() {
  if (isUpdating) return;
  isUpdating = true;

  try {
    const info = getDevToolsInfo();
    if (!info) {
      for (const [id, entry] of activePages) {
        try { entry.ws.close(); } catch (_) {}
      }
      activePages.clear();
      return 8000; // Antigravity not running, sleep 8s
    }

    if (currentPort !== info.port) {
      currentPort = info.port;
      for (const [id, entry] of activePages) {
        try { entry.ws.close(); } catch (_) {}
      }
      activePages.clear();
    }

    const pages = await listAntigravityPages(currentPort);
    const currentPageIds = new Set(pages.map(p => p.id));

    // Remove dead pages
    for (const [id, entry] of activePages) {
      if (!currentPageIds.has(id)) {
        try { entry.ws.close(); } catch (_) {}
        activePages.delete(id);
      }
    }

    // Connect new pages
    for (const page of pages) {
      const existing = activePages.get(page.id);
      if (!existing || existing.ws.readyState !== WebSocket.OPEN) {
        if (existing) {
          try { existing.ws.close(); } catch (_) {}
          activePages.delete(page.id);
        }
        await connectPageWebSocket(page);
      }
    }

    if (activePages.size === 0) return 4000; // No valid pages connected, sleep 4s

    let activeSessionId = null;
    for (const page of pages) {
      const m = page.url && page.url.match(/\/c\/([a-zA-Z0-9_-]+)/);
      if (m && m[1]) {
        activeSessionId = m[1];
        break;
      }
    }

    const stats = await statsWorker.fetchStats(activeSessionId);
    if (!stats) return 2500;

    const lang = getConfigLang();
    const payload = JSON.stringify(stats);
    const clientScript = getClientScript();

    for (const [id, entry] of activePages) {
      if (entry.ws.readyState !== WebSocket.OPEN) continue;

      let evalCode = "";
      let returnByValue = false;
      if (!entry.injected) {
        // Full initial injection once
        evalCode = `
          window.__AGY_DATA__ = ${payload};
          window.__AGY_LANG__ = ${JSON.stringify(lang)};
          ${clientScript};
          window.__AGY_HUD_INSTALLED__ = true;
        `;
        entry.injected = true;
      } else {
        // Fast granular data update (< 200 bytes) with context-loss detection
        returnByValue = true;
        evalCode = `
          (function() {
            window.__AGY_DATA__ = ${payload};
            window.__AGY_LANG__ = ${JSON.stringify(lang)};
            if (typeof window.__AGY_UPDATE__ === "function") {
              window.__AGY_UPDATE__(window.__AGY_DATA__, window.__AGY_LANG__);
              return "ok";
            }
            return "need_inject";
          })()
        `;
      }

      entry.ws.send(JSON.stringify({
        id: nextCdpId(),
        method: "Runtime.evaluate",
        params: {
          expression: evalCode,
          returnByValue
        }
      }));
    }

    return 2500; // Healthy cycle, check again in 2.5s
  } catch (_) {
    return 3000;
  } finally {
    isUpdating = false;
  }
}

// Adaptive polling loop
async function runScheduler() {
  const nextDelay = await updateLoop();
  setTimeout(runScheduler, nextDelay || 2500);
}

// Clean termination handling
process.on("SIGINT", () => {
  statsWorker.destroy();
  process.exit(0);
});
process.on("SIGTERM", () => {
  statsWorker.destroy();
  process.exit(0);
});
process.on("exit", () => {
  statsWorker.destroy();
});

console.log("[Antigravity Tokens HUD] Daemon running with Singleton Lock & Persistent Worker.");
runScheduler();
