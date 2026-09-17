#!/bin/bash

# Start dashboard API server in background
echo "🚀 Starting Dashboard API Server on http://localhost:8000..."
python -m backend.dashboard &
BACKEND_PID=$!

# Wait for backend to start
sleep 2

# Start frontend dev server
echo "🎨 Starting Frontend on http://localhost:5173..."
cd frontend
npm run dev

# Cleanup on exit
trap "kill $BACKEND_PID" EXIT
