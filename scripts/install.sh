#!/bin/bash

# Activity Tracker Installation Script
# Creates systemd user service and data directory structure

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get absolute path to project directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DATA_DIR="$HOME/activity-tracker-data"
SYSTEMD_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SYSTEMD_DIR/activity-tracker.service"

echo -e "${GREEN}Installing Activity Tracker...${NC}"

# Check for required dependencies
echo "Checking required dependencies..."

if which xdotool > /dev/null 2>&1; then
    echo -e "  ${GREEN}✓${NC} xdotool found (required for window detection and cropping)"
else
    echo -e "  ${RED}✗${NC} xdotool not found - Install: sudo apt install xdotool"
    echo -e "      ${YELLOW}Required for window geometry detection and screenshot cropping${NC}"
fi

echo

# Check for optional dependencies (summarization features)
echo "Checking optional dependencies for AI summarization..."

if which tesseract > /dev/null 2>&1; then
    echo -e "  ${GREEN}✓${NC} tesseract found"
else
    echo -e "  ${YELLOW}!${NC} tesseract not found - Install: sudo apt install tesseract-ocr"
fi

# Check for Ollama Docker container
if which docker > /dev/null 2>&1; then
    echo -e "  ${GREEN}✓${NC} docker found"
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^ollama$"; then
        echo -e "  ${GREEN}✓${NC} ollama container running"
        # Check if model is available
        if docker exec ollama ollama list 2>/dev/null | grep -q "gemma3:27b"; then
            echo -e "  ${GREEN}✓${NC} gemma3:27b model available"
        else
            echo -e "  ${YELLOW}!${NC} gemma3:27b model not found - Run:"
            echo -e "      docker exec ollama ollama pull gemma3:27b-it-qat"
            echo -e "      (or gemma3:14b-it-qat for 8GB VRAM cards)"
        fi
    else
        echo -e "  ${YELLOW}!${NC} ollama container not running - Start with:"
        echo -e "      docker run -d --gpus=all -v ollama:/root/.ollama -p 11434:11434 --name ollama ollama/ollama"
    fi
else
    echo -e "  ${YELLOW}!${NC} docker not found - Install Docker to use Ollama container"
fi

# Check Ollama API connectivity
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo -e "  ${GREEN}✓${NC} Ollama API responding at http://localhost:11434"
else
    echo -e "  ${YELLOW}!${NC} Ollama API not responding at http://localhost:11434"
fi

echo

# Create systemd user directory if it doesn't exist
echo "Creating systemd user directory..."
mkdir -p "$SYSTEMD_DIR"

# Create data directory structure
echo "Creating data directory structure..."
mkdir -p "$DATA_DIR"/{screenshots,logs}

# Ask user if they want web interface enabled
read -p "Enable web interface? (y/N): " ENABLE_WEB
EXEC_START="$PROJECT_DIR/venv/bin/python -m tracker.daemon"

if [[ "$ENABLE_WEB" =~ ^[Yy]$ ]]; then
    EXEC_START="$EXEC_START --web"
    echo "Web interface will be enabled on http://0.0.0.0:55555"
fi

# Ask user if they want auto-summarization enabled
read -p "Enable auto-summarization? (requires tesseract + ollama) (y/N): " ENABLE_SUMMARIZE
if [[ "$ENABLE_SUMMARIZE" =~ ^[Yy]$ ]]; then
    EXEC_START="$EXEC_START --auto-summarize"
    echo "Auto-summarization will generate hourly summaries at :05 past each hour"
fi

# Create systemd service file
echo "Creating systemd service file..."
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Activity Tracker Screenshot Daemon
After=graphical-session.target

[Service]
Type=simple
ExecStart=$EXEC_START
WorkingDirectory=$PROJECT_DIR
Environment=PYTHONPATH=$PROJECT_DIR
Environment=DISPLAY=:0
Environment=XDG_RUNTIME_DIR=/run/user/%U
Restart=always
RestartSec=10
StandardOutput=append:$DATA_DIR/logs/daemon.log
StandardError=append:$DATA_DIR/logs/daemon.log

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now activity-tracker
systemctl --user restart activity-tracker

echo -e "${GREEN}Installation complete!${NC}"
echo
echo "1. Check service status:"
echo "   systemctl --user status activity-tracker"
echo
echo "2. View logs:"
echo "   journalctl --user -u activity-tracker -f"
echo "   or: tail -f $DATA_DIR/logs/daemon.log"
echo
echo -e "${YELLOW}Data will be stored in:${NC} $DATA_DIR"
echo -e "${YELLOW}Service file created at:${NC} $SERVICE_FILE"

if [[ "$ENABLE_WEB" =~ ^[Yy]$ ]]; then
    echo
    echo -e "${GREEN}Web interface enabled!${NC}"
    echo -e "Access the activity viewer at: ${YELLOW}http://0.0.0.0:55555${NC}"
fi
