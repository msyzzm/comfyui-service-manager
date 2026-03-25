#!/bin/bash
# ComfyUI Service Manager - Update Script

set -e

SERVICE_NAME="comfyui-service-manager"
INSTALL_DIR="/home/aznable/${SERVICE_NAME}"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_info "Updating ${SERVICE_NAME}..."

# Check if running as root for systemctl commands
NEED_SUDO=false
if [ "$EUID" -ne 0 ]; then
    NEED_SUDO=true
    print_warn "Some commands require sudo"
fi

# Pull latest code
print_info "Pulling latest code..."
cd "${INSTALL_DIR}"
git pull

# Check if service file was updated
if git diff --name-only HEAD@{1} HEAD | grep -q "${SERVICE_NAME}.service"; then
    print_info "Service file updated, reinstalling..."
    if [ "$NEED_SUDO" = true ]; then
        sudo cp "${SERVICE_NAME}.service" "/etc/systemd/system/"
        sudo systemctl daemon-reload
    else
        cp "${SERVICE_NAME}.service" "/etc/systemd/system/"
        systemctl daemon-reload
    fi
fi

# Restart service
print_info "Restarting service..."
if [ "$NEED_SUDO" = true ]; then
    sudo systemctl restart "${SERVICE_NAME}"
else
    systemctl restart "${SERVICE_NAME}"
fi

print_info "Update completed!"
echo ""
if [ "$NEED_SUDO" = true ]; then
    systemctl status "${SERVICE_NAME}" --no-pager
else
    systemctl status "${SERVICE_NAME}" --no-pager
fi
