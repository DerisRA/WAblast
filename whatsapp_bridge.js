"use strict";

const readline = require("readline");
const path = require("path");
const fs = require("fs");
const { execFileSync } = require("child_process");

process.on("unhandledRejection", (reason) => {
  process.stderr.write(`[bridge] Ignored unhandledRejection: ${reason?.message || String(reason)}\n`);
});
process.on("uncaughtException", (err) => {
  process.stderr.write(`[bridge] Ignored uncaughtException: ${err?.message || String(err)}\n`);
});

const dataDir = process.env.WA_BLAST_DATA_DIR ||
  path.join(process.env.APPDATA || process.cwd(), "WAblast");
fs.mkdirSync(dataDir, { recursive: true });
const authDataPath = path.join(dataDir, "whatsapp_auth");
const authSessionPath = path.join(authDataPath, "session-wablast");
const pidFilePath = path.join(authDataPath, "browser.pid");

let lastBrowserPid = null;
let activeSessionId = 0;

function sleepSync(ms) {
  try {
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
  } catch (_) {}
}

function updateBrowserPid(inst) {
  try {
    const pid = inst?.pupBrowser?.process?.()?.pid;
    if (pid && pid !== lastBrowserPid) {
      lastBrowserPid = pid;
      fs.mkdirSync(authDataPath, { recursive: true });
      fs.writeFileSync(pidFilePath, String(pid), "utf8");
    }
  } catch (_) {}
}

function killKnownBrowserPids() {
  const pids = new Set();
  if (lastBrowserPid) pids.add(Number(lastBrowserPid));
  try {
    if (fs.existsSync(pidFilePath)) {
      const saved = Number(fs.readFileSync(pidFilePath, "utf8").trim());
      if (saved > 0) pids.add(saved);
      fs.unlinkSync(pidFilePath);
    }
  } catch (_) {}
  lastBrowserPid = null;

  if (process.platform === "win32" && pids.size > 0) {
    let killedAny = false;
    for (const pid of pids) {
      try {
        execFileSync("taskkill.exe", ["/PID", String(pid), "/T", "/F"], {
          windowsHide: true,
          stdio: "ignore",
          timeout: 4000
        });
        killedAny = true;
      } catch (_) {}
    }
    if (killedAny) {
      sleepSync(250);
    }
  }
}

function cleanSessionLockfiles() {
  let allCleared = true;
  for (const name of ["lockfile", "DevToolsActivePort"]) {
    const target = path.join(authSessionPath, name);
    try {
      if (fs.existsSync(target)) {
        fs.unlinkSync(target);
      }
    } catch (_) {
      if (name === "lockfile") {
        allCleared = false;
      }
    }
  }
  return allCleared;
}

function killOrphanBrowsersForSession(forceWmi = false) {
  if (process.platform !== "win32") return;
  killKnownBrowserPids();
  const locksCleared = cleanSessionLockfiles();

  if (!locksCleared || forceWmi) {
    try {
      const psScript = `$s = [wmisearcher]"SELECT ProcessId FROM Win32_Process WHERE (Name='msedge.exe' OR Name='chrome.exe') AND CommandLine LIKE '%session-wablast%'"; foreach ($p in $s.Get()) { & taskkill.exe /PID $p.ProcessId /T /F 2>$null | Out-Null }`;
      const b64 = Buffer.from(psScript, "utf16le").toString("base64");
      execFileSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-EncodedCommand", b64], {
        windowsHide: true,
        stdio: "ignore",
        timeout: 8000
      });
      sleepSync(300);
    } catch (_) {}
    cleanSessionLockfiles();
  }
}

async function safeDestroy(instance, forceWmi = false) {
  if (readyCheckInterval) {
    clearInterval(readyCheckInterval);
    readyCheckInterval = null;
  }
  if (instance) {
    instance._closing = true;
    updateBrowserPid(instance);
  }
  const browser = instance?.pupBrowser;
  if (browser) {
    try {
      await Promise.race([
        browser.close().catch(() => {}),
        new Promise((resolve) => setTimeout(resolve, 1500))
      ]);
    } catch (_) {}
  }
  killOrphanBrowsersForSession(forceWmi);
}

async function removeSessionDirectory() {
  if (!fs.existsSync(authSessionPath)) return;
  for (let i = 0; i < 6; i++) {
    try {
      fs.rmSync(authSessionPath, { recursive: true, force: true });
      if (!fs.existsSync(authSessionPath)) return;
    } catch (_) {
      if (i === 2) {
        killOrphanBrowsersForSession(true);
      }
      await new Promise((r) => setTimeout(r, 200));
    }
  }
}

function applyCompatibilityPatches() {
  try {
    const entryPath = require.resolve("whatsapp-web.js");
    const utilsPath = path.join(path.dirname(entryPath), "src", "util", "Injected", "Utils.js");
    const clientPath = path.join(path.dirname(entryPath), "src", "Client.js");

    // Patch 1: Media ID compatibility & bounded waitUntilMsgSent timeout
    // Patch 1: Media ID compatibility, bounded msgPromise/waitUntilMsgSent, and non-blocking getChat
    if (fs.existsSync(utilsPath)) {
      let source = fs.readFileSync(utilsPath, "utf8").replace(/\r\n/g, "\n");
      let utilsModified = false;
      const marker = "delete message.__x_id;";
      const anchor = "            ...extraOptions,\n        };\n\n        // Bot's won't reply if canonicalUrl is set (linking)";
      if (!source.includes(marker) && source.includes(anchor)) {
        source = source.replace(anchor, "            ...extraOptions,\n        };\n\n        delete message.__x_id;\n\n        // Bot's won't reply if canonicalUrl is set (linking)");
        utilsModified = true;
      }
      const msgTarget = "        const [msgPromise, sendMsgResultPromise] = window\n            .require('WAWebSendMsgChatAction')\n            .addAndSendMsgToChat(chat, message);\n        await msgPromise;";
      const msgReplacement = "        const [msgPromise, sendMsgResultPromise] = window\n            .require('WAWebSendMsgChatAction')\n            .addAndSendMsgToChat(chat, message);\n        await Promise.race([Promise.resolve(msgPromise).catch(() => {}), new Promise((r) => setTimeout(r, 2500))]);";
      if (source.includes(msgTarget)) {
        source = source.replace(msgTarget, msgReplacement);
        utilsModified = true;
      }
      const waitTarget = "if (options.waitUntilMsgSent) await sendMsgResultPromise;";
      const waitReplacement = "if (options.waitUntilMsgSent) { await Promise.race([Promise.resolve(sendMsgResultPromise).catch(() => {}), new Promise((r) => setTimeout(r, 5000))]); }";
      if (source.includes(waitTarget)) {
        source = source.replace(waitTarget, waitReplacement);
        utilsModified = true;
      }
      const chatTarget = "        } else {\n            chat =\n                window.require('WAWebCollections').Chat.get(chatWid) ||\n                (\n                    await window\n                        .require('WAWebFindChatAction')\n                        .findOrCreateLatestChat(chatWid)\n                )?.chat;\n        }";
      const chatReplacement = "        } else {\n            chat = window.require('WAWebCollections').Chat.get(chatWid);\n            if (!chat) {\n                try {\n                    const found = await Promise.race([\n                        window.require('WAWebFindChatAction').findOrCreateLatestChat(chatWid),\n                        new Promise((r) => setTimeout(() => r(null), 2500))\n                    ]);\n                    chat = found?.chat || window.require('WAWebCollections').Chat.get(chatWid);\n                } catch (_) {}\n                if (!chat) {\n                    try {\n                        const ChatCol = window.require('WAWebCollections').Chat;\n                        const added = ChatCol.add({ id: chatWid });\n                        chat = (Array.isArray(added) ? added[0] : added) || ChatCol.get(chatWid);\n                    } catch (_) {}\n                }\n            }\n        }";
      if (source.includes(chatTarget)) {
        source = source.replace(chatTarget, chatReplacement);
        utilsModified = true;
      }
      if (utilsModified) {
        fs.writeFileSync(utilsPath, source, "utf8");
      }
    }

    // Patch 2 & 3: Client.js compatibility (hasSynced, Puppeteer 24 isConnected, early pupBrowser, safe framenavigated)
    if (fs.existsSync(clientPath)) {
      let clientSource = fs.readFileSync(clientPath, "utf8").replace(/\r\n/g, "\n");
      let modified = false;

      const syncTarget = "            window\n                .require('WAWebSocketModel')\n                .Socket.on('change:hasSynced', () => {\n                    window.onAppStateHasSyncedEvent();\n                });";
      const syncReplacement = "            const _socket = window.require('WAWebSocketModel')?.Socket;\n            if (_socket) {\n                _socket.on('change:hasSynced', () => {\n                    window.onAppStateHasSyncedEvent();\n                });\n                if (_socket.hasSynced) {\n                    window.onAppStateHasSyncedEvent();\n                }\n            }";
      if (!clientSource.includes("if (_socket.hasSynced)") && clientSource.includes(syncTarget)) {
        clientSource = clientSource.replace(syncTarget, syncReplacement);
        modified = true;
      }

      const launchTarget = "            browser = await puppeteer.launch({\n                ...puppeteerOpts,\n                args: browserArgs,\n            });\n            page = (await browser.pages())[0];";
      const launchReplacement = "            browser = await puppeteer.launch({\n                ...puppeteerOpts,\n                args: browserArgs,\n            });\n            if (browser && typeof browser.isConnected !== 'function') {\n                browser.isConnected = () => Boolean(browser.connected);\n            }\n            this.pupBrowser = browser;\n            if (this._closing) {\n                await browser.close().catch(() => {});\n                throw new Error('Client closed during launch');\n            }\n            page = (await browser.pages())[0];";
      if (!clientSource.includes("Client closed during launch") && clientSource.includes(launchTarget)) {
        clientSource = clientSource.replace(launchTarget, launchReplacement);
        modified = true;
      }

      const destroyTarget = "        const isConnected = browser?.isConnected?.();";
      const destroyReplacement = "        const isConnected = typeof browser?.isConnected === 'function' ? browser.isConnected() : Boolean(browser?.connected);";
      if (clientSource.includes(destroyTarget)) {
        clientSource = clientSource.replace(destroyTarget, destroyReplacement);
        modified = true;
      }

      const navTarget = "        this.pupPage.on('framenavigated', async (frame) => {\n            if (frame.url().includes('post_logout=1') || this.lastLoggedOut) {\n                this.emit(Events.DISCONNECTED, 'LOGOUT');\n                await this.authStrategy.logout();\n                await this.authStrategy.beforeBrowserInitialized();\n                await this.authStrategy.afterBrowserInitialized();\n                this.lastLoggedOut = false;\n            }\n            await this.inject();\n        });";
      const navReplacement = "        this.pupPage.on('framenavigated', async (frame) => {\n            if (this._closing) return;\n            try {\n                if (frame.url().includes('post_logout=1') || this.lastLoggedOut) {\n                    if (this._closing) return;\n                    this.emit(Events.DISCONNECTED, 'LOGOUT');\n                    await this.authStrategy.logout();\n                    await this.authStrategy.beforeBrowserInitialized();\n                    await this.authStrategy.afterBrowserInitialized();\n                    this.lastLoggedOut = false;\n                }\n                if (!this._closing) {\n                    await this.inject();\n                }\n            } catch (_) {}\n        });";
      if (!clientSource.includes("if (this._closing) return;") && clientSource.includes(navTarget)) {
        clientSource = clientSource.replace(navTarget, navReplacement);
        modified = true;
      }

      if (modified) {
        fs.writeFileSync(clientPath, clientSource, "utf8");
      }
    }
  } catch (err) {
    process.stderr.write(`[bridge] Warning: Could not apply compatibility patches: ${err.message}\n`);
  }
}

applyCompatibilityPatches();

const { Client, LocalAuth, MessageMedia } = require("whatsapp-web.js");
const { LoadUtils } = require("whatsapp-web.js/src/util/Injected/Utils");
const QRCode = require("qrcode");

// Keep stdout reserved for the Python JSON-lines bridge.
const log = (...args) => process.stderr.write(args.join(" ") + "\n");
console.log = log;
console.info = log;
console.warn = log;

let client = null;
let state = "STOPPED";
let latestQr = null;
let lastAccount = null;
const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });

function emit(value) {
  process.stdout.write(JSON.stringify(value) + "\n");
}
function setState(value, detail, account) {
  state = value;
  if (account !== undefined) {
    lastAccount = account;
  }
  emit({ event: "status", status: state, detail: detail || "", account: lastAccount });
}
function reply(id, ok, result, error) {
  emit({ id, ok, result: result === undefined ? null : result, error: error || null });
}
function ready() {
  if (!client || state !== "WORKING") {
    throw new Error("WhatsApp belum terhubung. Pindai QR dan tunggu status siap.");
  }
}

let readyCheckInterval = null;

async function ensureWWebJSInjected(inst) {
  if (!inst || inst._closing || !inst.pupPage) return false;
  try {
    const hasWWebJS = await inst.pupPage.evaluate(() => typeof window.WWebJS !== "undefined");
    if (hasWWebJS) return true;
    await inst.pupPage.evaluate(LoadUtils);
    return await inst.pupPage.evaluate(() => typeof window.WWebJS !== "undefined");
  } catch (err) {
    log("[bridge] ensureWWebJSInjected warning:", err.message || String(err));
    return false;
  }
}

async function checkInPageReadiness() {
  const current = client;
  if (!current || current._closing || state === "WORKING" || state === "STOPPED") {
    if ((state === "WORKING" || state === "STOPPED") && readyCheckInterval) {
      clearInterval(readyCheckInterval);
      readyCheckInterval = null;
    }
    return;
  }
  try {
    if (!current.pupPage) return;
    const pageInfo = await current.pupPage.evaluate(() => {
      const hasMainPane = !!(
        document.querySelector("#pane-side") ||
        document.querySelector('div[data-testid="chat-list"]') ||
        document.querySelector('div[aria-label*="Chat"]')
      );
      let phone = null;
      let pushname = null;
      try {
        const me = window.require?.("WAWebUserPrefsMeUser");
        phone = me?.getMaybeMePnUser?.()?.user || me?.getMaybeMeLidUser?.()?.user;
        const conn = window.require?.("WAWebConnModel")?.Conn;
        if (!phone && conn?.wid) phone = conn.wid.user;
        if (!pushname && conn?.pushname) pushname = conn.pushname;
      } catch (_) {}
      return { hasMainPane, phone, pushname };
    });

    if (client !== current || current._closing) return;

    if (pageInfo && (pageInfo.hasMainPane || pageInfo.phone)) {
      await ensureWWebJSInjected(current);
      if (client !== current || current._closing) return;
      if (readyCheckInterval) {
        clearInterval(readyCheckInterval);
        readyCheckInterval = null;
      }
      latestQr = null;
      const phone = pageInfo.phone || current.info?.wid?.user || "";
      const pushname = pageInfo.pushname || current.info?.pushname || "";
      log(`[bridge] Terdeteksi antarmuka utama WhatsApp Web aktif. Siap digunakan! Phone: ${phone}`);
      setState("WORKING", "Terhubung - WhatsApp siap", { phone, pushname });
    }
  } catch (_) {}
}

async function connect(isRetry = false, retryCount = 0, sessionId = null) {
  if (!isRetry && client && !client._closing && ["STARTING", "SCAN_QR_CODE", "WORKING"].includes(state)) {
    return { status: state };
  }
  const mySessionId = sessionId !== null ? sessionId : (++activeSessionId);

  if (readyCheckInterval) {
    clearInterval(readyCheckInterval);
    readyCheckInterval = null;
  }
  if (client) {
    const prev = client;
    client = null;
    try { await safeDestroy(prev, false); } catch (_) {}
  }
  if (mySessionId !== activeSessionId) {
    return { status: state };
  }

  killOrphanBrowsersForSession(isRetry);
  latestQr = null;
  setState("STARTING", isRetry ? "Menyiapkan ulang sesi browser WhatsApp Web..." : "");

  const browserCandidates = [
    process.env.WABLAST_BROWSER,
    path.join(process.env["ProgramFiles(x86)"] || "C:\\Program Files (x86)", "Microsoft", "Edge", "Application", "msedge.exe"),
    path.join(process.env.ProgramFiles || "C:\\Program Files", "Microsoft", "Edge", "Application", "msedge.exe"),
    path.join(process.env.ProgramFiles || "C:\\Program Files", "Google", "Chrome", "Application", "chrome.exe"),
    path.join(process.env.LOCALAPPDATA || "", "Google", "Chrome", "Application", "chrome.exe")
  ].filter(Boolean);
  const browserPath = browserCandidates.find((candidate) => fs.existsSync(candidate));
  const puppeteerOptions = {
    headless: true,
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-dev-shm-usage",
      "--disable-accelerated-2d-canvas",
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-gpu",
      "--disable-background-timer-throttling",
      "--disable-backgrounding-occluded-windows",
      "--disable-renderer-backgrounding",
      "--disable-extensions",
      "--disable-default-apps",
      "--disable-component-extensions-with-background-pages",
      "--disable-features=msEdgeStartupBoost,msEdgeStartupBoostUserPreConsent,msEdgePreload,Translate",
      "--disable-background-mode",
      "--disable-background-networking",
      "--edge-skip-compat-layer-relaunch"
    ]
  };
  if (browserPath) puppeteerOptions.executablePath = browserPath;

  const instance = new Client({
    authStrategy: new LocalAuth({
      clientId: "wablast",
      dataPath: authDataPath,
      rmMaxRetries: 3
    }),
    userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    puppeteer: puppeteerOptions,
    authTimeoutMs: 60000
  });
  instance._closing = false;
  client = instance;

  readyCheckInterval = setInterval(() => {
    if (mySessionId !== activeSessionId || client !== instance || instance._closing) return;
    updateBrowserPid(instance);
    checkInPageReadiness();
  }, 1500);

  instance.on("qr", async (value) => {
    if (mySessionId !== activeSessionId || client !== instance || instance._closing) return;
    updateBrowserPid(instance);
    setState("SCAN_QR_CODE", "Menunggu pemindaian QR");
    try {
      const png = await QRCode.toBuffer(value, { type: "png", width: 320, margin: 2 });
      if (mySessionId !== activeSessionId || client !== instance || instance._closing) return;
      latestQr = png.toString("base64");
      emit({ event: "qr", data: latestQr });
    } catch (error) {
      emit({ event: "error", message: "Gagal membuat QR: " + error.message });
    }
  });

  instance.on("authenticated", () => {
    if (mySessionId !== activeSessionId || client !== instance || instance._closing) return;
    updateBrowserPid(instance);
    setState("STARTING", "Akun terverifikasi. Menyinkronkan chat...");
  });

  instance.on("loading_screen", (percent) => {
    if (mySessionId !== activeSessionId || client !== instance || instance._closing) return;
    updateBrowserPid(instance);
    if (state === "WORKING") return;
    const pct = typeof percent === "number" ? ` (${percent}%)` : "";
    setState("STARTING", `Menyinkronkan chat WhatsApp${pct}...`);
    if (percent === 100 || percent === "100") {
      setTimeout(checkInPageReadiness, 500);
    }
  });

  instance.on("ready", () => {
    if (mySessionId !== activeSessionId || client !== instance || instance._closing) return;
    updateBrowserPid(instance);
    if (readyCheckInterval) {
      clearInterval(readyCheckInterval);
      readyCheckInterval = null;
    }
    latestQr = null;
    const phone = instance.info?.wid?.user || "";
    const pushname = instance.info?.pushname || "";
    setState("WORKING", "Terhubung - WhatsApp siap", { phone, pushname });
  });

  instance.on("auth_failure", (message) => {
    if (mySessionId !== activeSessionId || client !== instance || instance._closing) return;
    setState("FAILED", String(message || "Autentikasi gagal"));
  });

  instance.on("disconnected", (reason) => {
    if (mySessionId !== activeSessionId || client !== instance || instance._closing) return;
    latestQr = null;
    setState("STOPPED", String(reason || "Sesi terputus"));
  });

  instance.initialize().then(() => {
    if (mySessionId === activeSessionId && client === instance && !instance._closing) {
      updateBrowserPid(instance);
    }
  }).catch(async (error) => {
    if (mySessionId !== activeSessionId || client !== instance || instance._closing) {
      return;
    }
    const message = error.message || String(error);
    log("WhatsApp Web initialization failed:", error.stack || message);

    if (retryCount < 2) {
      log(`[bridge] Gagal memulai sesi (percobaan ${retryCount + 1}/2). Membersihkan proses & lockfile lalu mencoba ulang...`);
      client = null;
      try {
        await safeDestroy(instance, true);
      } catch (_) {}
      await new Promise((r) => setTimeout(r, 600));
      if (mySessionId !== activeSessionId) return;
      return connect(true, retryCount + 1, mySessionId);
    }

    if (retryCount === 2 && fs.existsSync(authSessionPath)) {
      log("[bridge] Sesi lokal bermasalah setelah 2 percobaan ulang. Membersihkan sesi lokal dan meminta QR baru...");
      client = null;
      try {
        await safeDestroy(instance, true);
        await removeSessionDirectory();
      } catch (_) {}
      await new Promise((r) => setTimeout(r, 400));
      if (mySessionId !== activeSessionId) return;
      return connect(true, 3, mySessionId);
    }

    client = null;
    try { await safeDestroy(instance, true); } catch (_) {}
    const detail = /reading ['\"]Socket['\"]/.test(message)
      ? message + "\n\nWhatsApp Web tidak menyediakan Socket untuk sesi ini. Klik Logout WhatsApp untuk mereset sesi lokal, lalu Hubungkan kembali."
      : message;
    setState("FAILED", detail);
  });

  return { status: state };
}

async function performLogout(statusMessage, markWiped = false) {
  activeSessionId++;
  if (readyCheckInterval) {
    clearInterval(readyCheckInterval);
    readyCheckInterval = null;
  }
  const current = client;
  client = null;

  if (current) {
    current._closing = true;
    if (state === "WORKING" && current.pupPage) {
      try {
        await Promise.race([
          current.pupPage.evaluate(() => {
            try {
              const socket = window.require?.("WAWebSocketModel")?.Socket;
              if (socket && typeof socket.logout === "function") {
                return socket.logout();
              }
            } catch (_) {}
          }),
          new Promise((resolve) => setTimeout(resolve, 1200))
        ]);
      } catch (error) {
        log("Logout remote notice:", error.message || String(error));
      }
    }
    try {
      await safeDestroy(current, false);
    } catch (_) {}
  } else {
    killOrphanBrowsersForSession(false);
  }

  await removeSessionDirectory();

  latestQr = null;
  lastAccount = null;
  setState("STOPPED", statusMessage, null);
  if (markWiped) {
    return { status: state, wiped: true, remoteLogout: true };
  }
  return { status: state };
}

async function handle(message) {
  const { id, cmd, args = {} } = message;
  try {
    let result;
    switch (cmd) {
      case "connect":
        result = await connect();
        break;
      case "status": {
        const phone = client?.info?.wid?.user || (lastAccount?.phone || "");
        const pushname = client?.info?.pushname || (lastAccount?.pushname || "");
        const account = (phone || pushname) ? { phone, pushname } : null;
        result = { status: state, account };
        break;
      }
      case "qr":
        if (!latestQr) throw new Error("QR belum tersedia. Tekan Hubungkan dan tunggu sesi meminta pemindaian.");
        result = { data: latestQr };
        break;
      case "check_exists": {
        ready();
        await ensureWWebJSInjected(client);
        const rawPhone = String(args.phone || "").replace(/\D/g, "");
        const defaultChatId = rawPhone + "@c.us";
        try {
          const timeoutPromise = new Promise((resolve) => setTimeout(() => resolve("TIMEOUT"), 2500));
          const queryPromise = (async () => {
            try {
              return await client.getNumberId(rawPhone);
            } catch (err) {
              log("[bridge] getNumberId error:", err.message);
              return "ERROR";
            }
          })();

          const checkRes = await Promise.race([queryPromise, timeoutPromise]);
          if (checkRes === "TIMEOUT" || checkRes === "ERROR") {
            result = { numberExists: true, chatId: defaultChatId, fallback: true, reason: String(checkRes) };
          } else if (checkRes && checkRes._serialized) {
            result = { numberExists: true, chatId: checkRes._serialized };
          } else if (checkRes === null) {
            result = { numberExists: false, chatId: null };
          } else {
            result = { numberExists: true, chatId: defaultChatId, fallback: true };
          }
          if (result.numberExists && result.chatId && client?.pupPage) {
            try {
              await Promise.race([
                client.pupPage.evaluate(async (cid) => {
                  try {
                    await window.WWebJS?.getChat?.(cid, { getAsModel: false });
                  } catch (_) {}
                }, result.chatId),
                new Promise((r) => setTimeout(r, 2500))
              ]);
            } catch (_) {}
          }
        } catch (err) {
          log(`[bridge] check_exists error for ${rawPhone}: ${err.message}; fallback ke kirim langsung.`);
          result = { numberExists: true, chatId: defaultChatId, fallback: true, reason: err.message };
        }
        break;
      }
      case "send": {
        ready();
        await ensureWWebJSInjected(client);
        const rawPhone = String(args.phone || "").replace(/\D/g, "");
        let chatId = (args.chatId && typeof args.chatId === "string" && args.chatId.includes("@"))
          ? args.chatId
          : (rawPhone + "@c.us");
        if (args.checkFirst) {
          try {
            const checkRes = await Promise.race([
              client.getNumberId(rawPhone).catch(() => "ERROR"),
              new Promise((r) => setTimeout(() => r("TIMEOUT"), 1800))
            ]);
            if (checkRes === null) {
              throw new Error("Nomor tidak terdaftar di WhatsApp");
            }
            if (checkRes && checkRes._serialized) {
              chatId = checkRes._serialized;
            }
          } catch (err) {
            if (/tidak terdaftar/i.test(err.message || "")) throw err;
          }
        }
        const sendOptions = {
          caption: args.text || "",
          sendSeen: false,
          linkPreview: false,
          waitUntilMsgSent: false
        };
        const sendTimeoutMs = 20000;
        const sendTask = (async () => {
          if (args.image) {
            if (!fs.existsSync(args.image)) throw new Error("File gambar tidak ditemukan: " + args.image);
            const imageSize = fs.statSync(args.image).size;
            if (imageSize > 16 * 1024 * 1024) throw new Error("Ukuran gambar maksimal 16 MB.");
            const allowedImageTypes = new Set(["image/jpeg", "image/png", "image/webp"]);
            if (!allowedImageTypes.has(args.imageMime)) {
              throw new Error("Format gambar harus JPEG, PNG, atau WebP yang valid.");
            }
            const media = MessageMedia.fromFilePath(args.image);
            media.mimetype = args.imageMime;
            return await client.sendMessage(chatId, media, sendOptions);
          } else {
            return await client.sendMessage(chatId, args.text || "", sendOptions);
          }
        })();
        const timeoutTask = new Promise((_, reject) =>
          setTimeout(() => reject(new Error("Waktu pengiriman pesan habis (20 detik). Pastikan koneksi WhatsApp aktif.")), sendTimeoutMs)
        );
        const sent = await Promise.race([sendTask, timeoutTask]);
        const messageId = sent && sent.id ? sent.id._serialized : ("sent_" + Date.now());
        result = {
          messageId: messageId,
          ack: sent && typeof sent.ack === "number" ? sent.ack : 1
        };
        break;
      }
      case "logout":
        result = await performLogout("Telah logout dari WhatsApp", false);
        break;
      case "wipe_session":
        result = await performLogout("Sesi dihentikan", true);
        break;
      case "shutdown":
        activeSessionId++;
        if (client) {
          const current = client;
          client = null;
          current._closing = true;
          try { await safeDestroy(current, false); } catch (_) {}
        } else {
          killOrphanBrowsersForSession(false);
        }
        reply(id, true, { status: "STOPPED" });
        setTimeout(() => process.exit(0), 50);
        return;
      default:
        throw new Error("Perintah tidak dikenal: " + cmd);
    }
    reply(id, true, result);
  } catch (error) {
    reply(id, false, null, error.message || String(error));
  }
}

rl.on("line", (line) => {
  let message;
  try { message = JSON.parse(line); }
  catch (error) { emit({ event: "error", message: "JSON perintah tidak valid" }); return; }
  handle(message);
});
rl.on("close", async () => {
  activeSessionId++;
  if (client) {
    const current = client;
    client = null;
    current._closing = true;
    try { await safeDestroy(current, false); } catch (_) {}
  }
  process.exit(0);
});
