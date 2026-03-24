#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "🔨 Cleaning previous build..."
rm -rf build dist

echo "📦 Building TokenBar.app..."
python3 setup.py py2app --dist-dir dist 2>&1 | tail -1

echo "🚀 Installing to /Applications..."
rm -rf /Applications/TokenBar.app
cp -R dist/TokenBar.app /Applications/TokenBar.app

echo "✅ Done! TokenBar.app installed in /Applications"
echo "   Launch it from Spotlight or Finder."
