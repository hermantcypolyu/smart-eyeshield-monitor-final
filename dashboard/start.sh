#!/bin/bash
# Smart Eye-Shield Dashboard Launcher
# Automatically installs dependencies and starts the dashboard server

set -e

cd "$(dirname "$0")/.."

echo "======================================================================"
echo "Smart Eye-Shield Safety Dashboard"
echo "======================================================================"
echo ""

# Check if websockets is installed
if ! .venv/bin/python -c "import websockets" 2>/dev/null; then
    echo "📦 Installing websockets package..."
    .venv/bin/pip install -q websockets
    echo "✅ websockets installed"
    echo ""
fi

echo "🚀 Starting dashboard server on ws://localhost:8765"
echo ""
echo "Open the dashboard in your browser:"
echo "  file://$(pwd)/dashboard/index.html"
echo ""
echo "Or run: open dashboard/index.html"
echo ""
echo "Press Ctrl+C to stop the server"
echo "======================================================================"
echo ""

.venv/bin/python tools/dashboard_server.py
