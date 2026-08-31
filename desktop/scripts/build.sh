#!/usr/bin/env bash
# Build script for the Electron desktop app.
# 1. Builds the frontend (Vite production build)
# 2. Compiles the Electron TypeScript
# 3. Copies frontend dist into desktop/dist/frontend/ (so it's inside the asar)
# 4. Packages into an unsigned .exe via electron-builder

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Building frontend ==="
cd "$PROJECT_ROOT/frontend"
pnpm build

echo "=== Compiling Electron TypeScript ==="
cd "$PROJECT_ROOT/desktop"
pnpm build

echo "=== Copying frontend dist into desktop/dist/frontend/ ==="
mkdir -p "$PROJECT_ROOT/desktop/dist/frontend"
cp -r "$PROJECT_ROOT/frontend/dist/"* "$PROJECT_ROOT/desktop/dist/frontend/"

echo "=== Packaging with electron-builder ==="
cd "$PROJECT_ROOT/desktop"
npx electron-builder --win --dir

echo "=== Done! ==="
echo "Output: $PROJECT_ROOT/desktop/dist/win-unpacked/"
