# ADR-012: Desktop Packaging (.exe via Electron)

**Status:** Accepted
**Date:** 2026-08-31
**Deciders:** System Architect
**Supersedes:** None
**Related:** ADR-010 (Frontend Technology Stack), ADR-011 (Bilingual Support)

---

## 1. Context

The desktop app is a thin shell around the existing web frontend. It connects
to the same central FastAPI backend over the network — no local database, no
offline mode. This preserves every multi-user flow (Order approval, shared
inventory, Bale/Telegram bot channels, commission calculation) unchanged,
since those all require a single central source of truth per CLAUDE.md's own
domain rules.

### What already exists

- Complete React 19 + TypeScript + Vite frontend under `frontend/`
- JWT bearer authentication stored in `localStorage`
- `CurrentUserResponse` with `portal` field for role-based routing
- All 85 frontend tests passing
- No desktop packaging infrastructure

### What does not exist

- Any Electron configuration or main process code
- Any packaging tooling (electron-builder, electron-forge, etc.)
- Any backend URL configuration mechanism (frontend assumes proxy or
  `VITE_API_BASE_URL` env var)
- Any native OS integration (tray, menus, file system)

---

## 2. Decision: Shell Technology

### Chosen: Electron + electron-builder

| Aspect | Choice | Rationale |
|--------|--------|-----------|
| **Shell** | Electron | Mature, works directly with existing Vite/React build output. Well-documented for wrapping a web app that talks to a remote API. Team is already in the npm/TypeScript ecosystem — no new toolchain. |
| **Packager** | electron-builder | Industry standard for Electron packaging. Supports NSIS (Windows installer), DMG (macOS), AppImage/deb (Linux). Auto-update support available when needed later. |
| **Build integration** | Separate `desktop/` package | Electron config lives in its own `desktop/` directory, not merged into `frontend/`'s `package.json`. The desktop package imports the built frontend output. |

**Rejected alternatives:**

- **Tauri:** Requires Rust toolchain. While smaller binary size and better
  security model, adding Rust to a TypeScript/Python project for a thin web
  wrapper is disproportionate complexity. The app has no local compute-heavy
  work that would justify Rust. Can be reconsidered if native performance
  becomes a requirement.
- **NW.js:** Less maintained than Electron. Smaller ecosystem. No compelling
  advantage for this use case.
- **Neutralinojs:** Lightweight but limited ecosystem, fewer packaging options,
  less community support for enterprise use cases.
- **PWA / TWA (Trusted Web Activity):** No offline mode needed, but PWA
  installation UX is inconsistent across browsers, and there's no way to
  configure a backend URL through a PWA's own UI (would need a settings page
  in the web app itself, coupling desktop and web concerns).

---

## 3. Decision: Backend URL Configuration

### Chosen: First-run settings screen + electron-store persistence

**Problem:** The desktop app must NOT hardcode a server URL. Representatives
may be on different networks or connect to different server instances.

**Solution:**

1. **First-run screen:** On first launch (no saved URL), the Electron app
   shows a settings screen (a simple HTML page, not the React app) where the
   user enters the backend base URL (e.g., `https://api.example.com`).

2. **Persistence:** The URL is saved via `electron-store` to the OS app-data
   directory:
   - Windows: `%APPDATA%/enterprise-erp/config.json`
   - macOS: `~/Library/Application Support/enterprise-erp/config.json`
   - Linux: `~/.config/enterprise-erp/config.json`

3. **Loading flow:** Electron main process reads the stored URL, injects it
   as an environment variable (`BACKEND_URL`) into the renderer process, and
   loads the React app. The React app reads `BACKEND_URL` instead of
   `VITE_API_BASE_URL`.

4. **Settings change:** A menu item or system tray option allows changing the
   URL after initial setup (reopens the settings screen).

**Why `electron-store` + OS app-data, not `localStorage`:**
- `localStorage` in Electron's renderer is tied to the webview's storage,
  which doesn't persist reliably across Electron packaging and updates.
- OS app-data directory is the standard location for Electron app configuration.
- `electron-store` handles serialization, defaults, and platform paths.

**Why a separate settings screen (not part of the React app):**
- The React app needs a working backend URL to bootstrap (auth, API client
  generation). If the URL is wrong or missing, the React app can't render
  meaningful content.
- A standalone HTML settings page avoids the chicken-and-egg problem.

---

## 4. Decision: Security Baseline

### Chosen: Standard Electron security hardening

| Setting | Value | Rationale |
|---------|-------|-----------|
| `contextIsolation` | `true` | Renderer process runs in an isolated JavaScript context. Prevents prototype pollution attacks from injected content. |
| `nodeIntegration` | `false` | Renderer cannot access Node.js APIs directly. All Node.js operations happen in the main process via IPC. |
| `sandbox` | `true` | Additional process-level sandboxing (Chromium's sandbox). Limits system call access. |
| `webSecurity` | `true` | Same-origin policy enforced. Prevents loading cross-origin content in the renderer. |
| `allowRunningInsecureContent` | `false` | Blocks mixed HTTP/HTTPS content. |

**IPC surface:** Minimal. The renderer communicates with the main process
only for:
- Reading the saved backend URL (one IPC channel: `get-config`)
- Writing a new backend URL (one IPC channel: `set-config`)

No filesystem access, no shell execution, no native menu customization, no
tray icon in Phase 1.

**JWT auth flow:** Stays entirely client-side in the renderer process against
the remote API. The Electron shell doesn't need its own auth logic — it's
just a browser with a configurable URL bar.

**Code signing:** Explicitly out of scope for Phase 1. The .exe will be
unsigned. Users will see a Windows SmartScreen warning on first run, which
is standard for unsigned enterprise internal tools. Code signing is a
separate future concern requiring a code signing certificate.

---

## 5. Decision: Auto-Update

### Out of scope for this pass

Distributing new .exe builds is **manual** for now. There is no auto-update
mechanism. This is documented as a future ADR if it becomes a real need.

**When auto-update is needed:** `electron-builder` supports `electron-updater`
out of the box with GitHub Releases, S3, or generic HTTP endpoints. Adding
it later is a non-breaking addition to the `desktop/` package.

---

## 6. Decision: Target Platform & Packaging

### Chosen: Windows .exe via electron-builder NSIS target

| Aspect | Choice | Rationale |
|--------|--------|-----------|
| **Primary target** | Windows .exe (NSIS installer) | Primary user base is Iranian office/field representatives on Windows machines. NSIS produces a standard Windows installer with Start Menu shortcuts. |
| **Package location** | `desktop/` (separate top-level directory) | Electron config, main process, preload scripts, and build config live in `desktop/`. The `frontend/` package remains untouched — it's consumed as a build artifact. |
| **Build flow** | `frontend/` builds first → `desktop/` packages the output | `desktop/package.json` has a `build` script that first runs `pnpm --filter frontend build`, then runs `electron-builder` on the output. |
| **Output** | `desktop/dist/` → unsigned `.exe` installer | NSIS installer + unpacked directory. Both are build artifacts. |

**Why separate package, not merged into `frontend/`:**
- Electron introduces its own dependencies (`electron`, `electron-builder`,
  `electron-store`) that are irrelevant to the web frontend.
- The web frontend's `package.json` stays clean for Vercel/Netlify/similar
  deployment if a pure web deployment is ever needed alongside the desktop
  build.
- CI/CD can build the web frontend independently without pulling Electron
  dependencies.

---

## 7. Architecture Overview

```
desktop/
├── package.json              # Electron deps + build scripts
├── electron-builder.yml      # NSIS target config
├── src/
│   ├── main.ts               # Electron main process
│   ├── preload.ts            # Context bridge (get-config, set-config)
│   └── settings.html         # First-run backend URL settings page
├── scripts/
│   └── build.sh              # Build frontend, then package
└── dist/                     # Build output (.exe)
```

**Main process (`main.ts`):**
1. Read backend URL from `electron-store`
2. If no URL → show `settings.html` in a BrowserWindow
3. If URL exists → load `file://path-to-frontend-build/index.html` with
   `BACKEND_URL` injected via `preload.ts` context bridge
4. Register IPC handlers for `get-config` / `set-config`

**Preload script (`preload.ts`):**
- Exposes `window.electronAPI.getConfig()` and
  `window.electronAPI.setConfig(url)` via `contextBridge.exposeInMainWorld()`
- No Node.js APIs exposed to renderer

**Settings page (`settings.html`):**
- Standalone HTML (no React, no build step)
- Single input field for backend URL + "Save & Continue" button
- Validates URL format before saving
- On save: stores URL via IPC, reloads the main window with the React app

**React app integration:**
- `frontend/src/api/client.ts` reads `window.electronAPI?.getConfig()` or
  falls back to `import.meta.env.VITE_API_BASE_URL`
- This is a minimal, backward-compatible change — the web frontend continues
  working identically without Electron

---

## 8. Scope: Phase 1 (This Pass)

### Deliverables

1. **New `desktop/` package:**
   - `package.json` with Electron + electron-builder dependencies
   - `electron-builder.yml` with NSIS Windows target config
   - `src/main.ts` — Electron main process (config read, window management)
   - `src/preload.ts` — Context bridge for config IPC
   - `src/settings.html` — First-run backend URL settings page

2. **Frontend integration (minimal):**
   - `frontend/src/api/client.ts` updated to read `BACKEND_URL` from
     `window.electronAPI` when available, falling back to existing env var

3. **Build scripts:**
   - `desktop/scripts/build.sh` — builds frontend, then packages with
     electron-builder
   - `desktop/package.json` scripts: `dev`, `build`, `package`

4. **Manual verification:**
   - `.exe` opens and shows the settings page on first run
   - Entering a valid backend URL saves it and loads the React app
   - Successful login against a running backend
   - Window closes and reopens with the saved URL (persistence verified)

### Explicitly out of scope (Phase 1)

- Auto-update mechanism
- Native menu customization
- Tray icon
- Code signing
- macOS DMG / Linux AppImage targets
- Offline mode or local caching
- IPC channels beyond `get-config` / `set-config`
- Settings page redesign (bare minimum functional)

---

## 9. Future Phases (Not Implemented in This Pass)

| Phase | Scope |
|-------|-------|
| Phase 2 | macOS DMG + Linux AppImage targets |
| Phase 3 | Auto-update via electron-updater + GitHub Releases |
| Phase 4 | Native menu customization (File > Settings, View > Reload) |
| Phase 5 | Tray icon + minimize-to-tray |
| Phase 6 | Code signing (requires certificate purchase) |

---

## 10. Schema Changes

NONE. No backend changes. No database changes. No API changes.

---

## 11. Production Code Changes

Phase 1 only:
- New `desktop/` directory with Electron configuration
- Modified `frontend/src/api/client.ts` to support Electron-injected URL
  (backward-compatible, no behavior change for web deployment)
