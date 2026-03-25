#!/bin/bash
# ComfyUI Service Manager - Systemd Service Uninstallation Script

set -e

SERVICE_NAME="comfyui-service-manager"
INSTALL_DIR="/home/aznable/${SERVICE_NAME}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    print_error "Please run this script with sudo"
    exit 1
fi

print_warn "This will uninstall the ${SERVICE_NAME} systemd service"
read -p "Continue? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    print_info "Uninstallation cancelled"
    exit 0
fi

# Stop and disable service
print_info "Stopping service..."
systemctl stop "${SERVICE_NAME}" 2>/dev/null || true

print_info "Disabling service..."
systemctl disable "${SERVICE_NAME}" 2>/dev/null || true

# Remove service file
print_info "Removing systemd service file..."
rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload

# Ask about removing installation directory
echo ""
print_warn "Installation directory: ${INSTALL_DIR}"
read -p "Remove installation directory? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    rm -rf "${INSTALL_DIR}"
    print_info "Removed installation directory"
else
    print_info "Kept installation directory"
fi

print_info "Uninstallation completed successfully!"
