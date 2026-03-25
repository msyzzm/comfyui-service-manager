#!/bin/bash
# ComfyUI Service Manager - Systemd Service Installation Script

set -e

# Configuration
SERVICE_NAME="comfyui-service-manager"
SERVICE_FILE="${SERVICE_NAME}.service"
INSTALL_DIR="/home/aznable/${SERVICE_NAME}"
PYTHON_EXECUTABLE="/home/aznable/miniconda3/envs/comfy/bin/python"
HTTP_PORT=9999

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

print_info "Installing ${SERVICE_NAME} systemd service..."

# Check if service file exists
if [ ! -f "${SERVICE_FILE}" ]; then
    print_error "Service file '${SERVICE_FILE}' not found in current directory"
    exit 1
fi

# Check if Python executable exists
if [ ! -f "${PYTHON_EXECUTABLE}" ]; then
    print_error "Python executable not found: ${PYTHON_EXECUTABLE}"
    print_warn "Please update PYTHON_EXECUTABLE in this script"
    exit 1
fi

# Create working directory if it doesn't exist
if [ ! -d "${INSTALL_DIR}" ]; then
    print_warn "Installation directory does not exist: ${INSTALL_DIR}"
    read -p "Create it? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        mkdir -p "${INSTALL_DIR}"
        print_info "Created directory: ${INSTALL_DIR}"
    else
        print_error "Installation cancelled"
        exit 1
    fi
fi

# Copy files to installation directory (only if not already there)
CURRENT_DIR=$(pwd)
if [ "${CURRENT_DIR}" = "${INSTALL_DIR}" ]; then
    print_info "Already in installation directory, skipping copy..."
else
    print_info "Copying files to ${INSTALL_DIR}..."
    cp -r . "${INSTALL_DIR}/"
    chown -R aznable:aznable "${INSTALL_DIR}"
fi

# Make scripts executable
chmod +x "${INSTALL_DIR}/comfyui_service_manager.py"

# Install service file
print_info "Installing systemd service file..."
cp "${SERVICE_FILE}" "/etc/systemd/system/"

# Reload systemd
print_info "Reloading systemd daemon..."
systemctl daemon-reload

# Enable service (but don't start it yet)
print_info "Enabling service..."
systemctl enable "${SERVICE_NAME}"

echo ""
print_info "Installation completed successfully!"
echo ""
echo "Available commands:"
echo "  sudo systemctl start ${SERVICE_NAME}     # Start the service"
echo "  sudo systemctl stop ${SERVICE_NAME}      # Stop the service"
echo "  sudo systemctl restart ${SERVICE_NAME}   # Restart the service"
echo "  sudo systemctl status ${SERVICE_NAME}    # Check service status"
echo "  sudo journalctl -u ${SERVICE_NAME} -f    # View service logs"
echo ""
print_warn "Service is enabled but not started. Start it with:"
echo "  sudo systemctl start ${SERVICE_NAME}"
