import { app, BrowserWindow, ipcMain } from "electron";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ElectronStore from "electron-store";

// ESM equivalent of __dirname
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// ---------------------------------------------------------------------------
// Diagnostic helpers — attach to every BrowserWindow so we can trace the
// "C:/" navigation from the main-process console (DevTools won't show it).
// ---------------------------------------------------------------------------

function attachDiagnostics(win: BrowserWindow) {
  const tag = `[window ${win.id}]`;

  // 1. Log every failed load (network errors, missing files, CORS, etc.)
  win.webContents.on(
    "did-fail-load",
    (_event, errorCode, errorDescription, validatedURL, isMainFrame) => {
      console.error(`${tag} [did-fail-load]`, {
        errorCode,
        errorDescription,
        validatedURL,
        isMainFrame,
      });
    },
  );

  // 2. Log navigation attempts (this is how we'll find the "/" culprit)
  win.webContents.on("will-navigate", (event, url) => {
    console.warn(`${tag} [will-navigate attempted]`, url);
    console.warn(`${tag} [will-navigate stack]`, new Error().stack);

    // Block navigation away from the current file (under file:// protocol
    // an absolute "/" resolves to C:/ and kills the app).
    try {
      const currentURL = win.webContents.getURL();
      const current = new URL(currentURL);
      const target = new URL(url);
      const sameFile =
        target.protocol === current.protocol &&
        target.pathname === current.pathname;
      if (!sameFile) {
        console.warn(`${tag} [will-navigate BLOCKED]`, url);
        event.preventDefault();
      }
    } catch {
      console.warn(`${tag} [will-navigate BLOCKED (parse error)]`, url);
      event.preventDefault();
    }
  });

  // 3. Log redirect attempts
  win.webContents.on("will-redirect", (event, url) => {
    console.warn(`${tag} [will-redirect attempted]`, url);
    console.warn(`${tag} [will-redirect stack]`, new Error().stack);
    // Do NOT prevent — only log, so we can see what triggers it.
  });

  // 4. Log any unhandled console messages from the renderer
  win.webContents.on("console-message", (_event, level, message, line, sourceId) => {
    const levelStr = ["verbose", "info", "warning", "error"][level] ?? level;
    console.log(`${tag} [renderer ${levelStr}]`, message, `(${sourceId}:${line})`);
  });
}

// ---------------------------------------------------------------------------
// Config store — persisted in OS app-data directory
// ---------------------------------------------------------------------------

interface AppConfig {
  backendUrl: string | null;
}

const store = new ElectronStore<AppConfig>({
  name: "config",
  defaults: {
    backendUrl: null,
  },
});

// ---------------------------------------------------------------------------
// IPC handlers — minimal surface (get-config / set-config only)
// ---------------------------------------------------------------------------

ipcMain.handle("get-config", () => ({
  backendUrl: store.get("backendUrl"),
}));

ipcMain.on("get-backend-url-sync", (event) => {
  event.returnValue = store.get("backendUrl");
});

ipcMain.handle("set-config", (_event, config: { backendUrl: string }) => {
  store.set("backendUrl", config.backendUrl);
  return { ok: true };
});

// ---------------------------------------------------------------------------
// Window management
// ---------------------------------------------------------------------------

let mainWindow: BrowserWindow | null = null;

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    title: "Enterprise ERP",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  // Remove the default menu bar for a cleaner app feel
  mainWindow.setMenuBarVisibility(false);

  // Attach diagnostic listeners (did-fail-load, will-navigate, etc.)
  attachDiagnostics(mainWindow);

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function loadApp() {
  if (!mainWindow) return;

  const backendUrl = store.get("backendUrl");

  if (!backendUrl) {
    // No URL configured — show the first-run settings page.
    // settings.html lives in src/ (packaged by electron-builder from src/settings.html)
    // while __dirname points to the dist/ directory inside the asar.
    const settingsPath = path.join(__dirname, "..", "src", "settings.html");
    console.log("[loadApp] loading settings:", settingsPath);
    mainWindow.loadFile(settingsPath);
  } else {
    // URL configured — load the built frontend with the URL injected.
    const frontendPath = path.join(__dirname, "frontend", "index.html");
    console.log("[loadApp] loading frontend:", frontendPath);
    mainWindow.loadFile(frontendPath);
  }
}

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

app.whenReady().then(() => {
  createMainWindow();
  loadApp();

  // macOS: re-create window when dock icon is clicked
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
      loadApp();
    }
  });
});

app.on("window-all-closed", () => {
  // On macOS, apps typically stay active until Cmd+Q
  if (process.platform !== "darwin") {
    app.quit();
  }
});

// IPC handler for reloading after settings change
ipcMain.handle("reload-app", () => {
  loadApp();
});
