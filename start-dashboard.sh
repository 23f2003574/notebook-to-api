#!/bin/bash

# Start dashboard API server in background
echo "🚀 Starting Dashboard API Server on http://localhost:8001..."
python -m backend.dashboard &
BACKEND_PID=$!

# Wait for backend to start; it exits at once when its configuration is
# refused (e.g. a non-loopback NOTEBOOK_API_DASHBOARD_HOST without
# NOTEBOOK_API_DASHBOARD_KEY), in which case do not start the frontend.
sleep 2
if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo "❌ Dashboard API Server failed to start; see the error above."
    exit 1
fi

# Start frontend dev server
echo "🎨 Starting Frontend on http://localhost:5173..."
cd frontend
npm run dev

# Cleanup on exit
trap "kill $BACKEND_PID" EXIT
