import { app, BrowserWindow, ipcMain } from "electron";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ElectronStore from "electron-store";

// ESM equivalent of __dirname
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

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

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function loadApp() {
  if (!mainWindow) return;

  const backendUrl = store.get("backendUrl");

  if (!backendUrl) {
    // No URL configured — show the first-run settings page
    mainWindow.loadFile(path.join(__dirname, "settings.html"));
  } else {
    // URL configured — load the built frontend with the URL injected.
    mainWindow.loadFile(
      path.join(__dirname, "frontend/index.html"),
    );
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
