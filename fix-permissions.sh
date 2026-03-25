#!/bin/bash
# Fix permissions for ComfyUI after running as root

set -e

# Configuration
COMFYUI_DIR="/home/aznable/ComfyUI"
SERVICE_USER="aznable"
SERVICE_GROUP="aznable"

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

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    print_error "Please run this script with sudo"
    exit 1
fi

print_info "Fixing permissions for ComfyUI directory..."

# Check ComfyUI directory exists
if [ ! -d "$COMFYUI_DIR" ]; then
    print_error "ComfyUI directory not found: $COMFYUI_DIR"
    exit 1
fi

# Show current ownership issues
print_warn "Checking for files owned by root..."
echo ""

ROOT_FILES=$(find "$COMFYUI_DIR" -user root 2>/dev/null | wc -l)
if [ "$ROOT_FILES" -gt 0 ]; then
    print_warn "Found $ROOT_FILES files owned by root"
    echo ""
    print_info "Examples:"
    find "$COMFYUI_DIR" -user root 2>/dev/null | head -10
    echo ""
else
    print_info "No root-owned files found"
fi

# Ask for confirmation
echo ""
read -p "Fix all permissions in $COMFYUI_DIR? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    print_info "Cancelled"
    exit 0
fi

# Stop services first
print_info "Stopping ComfyUI services..."
sudo -u "$SERVICE_USER" systemctl stop comfyui-service-manager 2>/dev/null || true
pkill -u "$SERVICE_USER" -f "python.*main.py" 2>/dev/null || true

# Fix ownership
print_info "Changing ownership to $SERVICE_USER:$SERVICE_GROUP..."
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$COMFYUI_DIR"

# Fix permissions for directories
print_info "Setting directory permissions (755)..."
find "$COMFYUI_DIR" -type d -exec chmod 755 {} \;

# Fix permissions for files
print_info "Setting file permissions (644)..."
find "$COMFYUI_DIR" -type f -exec chmod 644 {} \;

# Make scripts executable
print_info "Making scripts executable..."
find "$COMFYUI_DIR" -name "*.py" -exec chmod +x {} \;

# Special handling for common directories
print_info "Ensuring write permissions for important directories..."
chmod 777 "$COMFYUI_DIR/input" 2>/dev/null || mkdir -p "$COMFYUI_DIR/input"
chmod 777 "$COMFYUI_DIR/output" 2>/dev/null || mkdir -p "$COMFYUI_DIR/output"
chmod 777 "$COMFYUI_DIR/temp" 2>/dev/null || mkdir -p "$COMFYUI_DIR/temp"

# Fix models directory permissions
if [ -d "$COMFYUI_DIR/models" ]; then
    print_info "Fixing models directory..."
    find "$COMFYUI_DIR/models" -type d -exec chmod 755 {} \;
    find "$COMFYUI_DIR/models" -type f -exec chmod 644 {} \;
fi

# Clean up any problematic files that might cause issues
print_info "Cleaning up problematic cache files..."
rm -f "$COMFYUI_DIR/.lock" 2>/dev/null || true
rm -rf "$COMFYUI_DIR/__pycache__" 2>/dev/null || true

echo ""
print_info "Permissions fixed successfully!"
echo ""
echo "Summary:"
echo "  ComfyUI directory: $COMFYUI_DIR"
echo "  Owner: $SERVICE_USER:$SERVICE_GROUP"
echo "  Directories: 755 (rwxr-xr-x)"
echo "  Files: 644 (rw-r--r--)"
echo "  Input/Output/Temp: 777 (rwxrwxrwx)"
echo ""
print_info "You can now start the service:"
echo "  sudo systemctl start comfyui-service-manager"
