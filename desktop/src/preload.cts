import { contextBridge, ipcRenderer } from "electron";

/**
 * Preload script — runs before the renderer's page scripts.
 *
 * Exposes a minimal API to the renderer process via contextBridge.
 * The renderer reads the backend URL asynchronously on first API request.
 *
 * Security model:
 * - contextIsolation: true (preload and page have separate window objects)
 * - nodeIntegration: false (no require/process in renderer)
 * - Only two IPC channels exposed: get-config and set-config
 * - No filesystem, shell, or native API access
 */
contextBridge.exposeInMainWorld("electronAPI", {
  /**
   * Get the saved backend URL. Returns null if not yet configured.
   */
  getConfig: (): Promise<{ backendUrl: string | null }> =>
    ipcRenderer.invoke("get-config"),

  /**
   * Save the backend URL and trigger a reload of the main window.
   */
  setConfig: (config: { backendUrl: string }): Promise<{ ok: boolean }> =>
    ipcRenderer.invoke("set-config", config),

  /**
   * Reload the app after settings change.
   */
  reloadApp: (): Promise<void> =>
    ipcRenderer.invoke("reload-app"),
});

// Set window.__BACKEND_URL__ synchronously, before any renderer page scripts
// run, so the frontend's API client can read it at module-init time.
contextBridge.exposeInMainWorld(
  "__BACKEND_URL__",
  ipcRenderer.sendSync("get-backend-url-sync"),
);

export interface ElectronAPI {
  getConfig: () => Promise<{ backendUrl: string | null }>;
  setConfig: (config: { backendUrl: string }) => Promise<{ ok: boolean }>;
  reloadApp: () => Promise<void>;
}