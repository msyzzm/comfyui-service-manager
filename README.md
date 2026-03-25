# ComfyUI Service Manager

Manage multiple ComfyUI instances with different configurations. Switch between services dynamically to optimize resource usage.

## Features

- **Multiple Service Management**: Run different ComfyUI configurations (normal, highvram, no-cache, etc.)
- **HTTP API Control**: RESTful API to start, stop, and switch services
- **Process Persistence**: Tracks PIDs across service manager restarts
- **Graceful Shutdown**: Properly stops services before switching
- **Cross-Platform**: Works on Linux, Windows, and macOS

## Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/comfyui-service-manager.git
cd comfyui-service-manager

# Install dependencies
pip install -r requirements.txt
```

## Quick Start

### 1. Configure Services

Copy the template configuration and edit it:

```bash
cp config/services.json.template config/services.json
# Edit config/services.json with your settings
```

```json
{
  "http_port": 9999,
  "services": [
    {
      "name": "normal",
      "port": 8188,
      "python_executable": "/home/aznable/miniconda3/envs/comfy/bin/python",
      "listen_host": "0.0.0.0",
      "vram_mode": "--normalvram",
      "extra_args": ["--listen", "--force-fp16", "--use-pytorch-cross-attention"],
      "work_dir": "/home/aznable/ComfyUI",
      "env_vars": {
        "HSA_OVERRIDE_GFX_VERSION": "11.0.0",
        "PYTORCH_HIP_ALLOC_CONF": "expandable_segments:True"
      }
    },
    {
      "name": "no-cache",
      "port": 8188,
      "python_executable": "/home/aznable/miniconda3/envs/comfy/bin/python",
      "listen_host": "0.0.0.0",
      "vram_mode": "--normalvram",
      "extra_args": ["--listen", "--force-fp16", "--use-pytorch-cross-attention", "--cache-none"],
      "work_dir": "/home/aznable/ComfyUI",
      "env_vars": {
        "HSA_OVERRIDE_GFX_VERSION": "11.0.0",
        "PYTORCH_HIP_ALLOC_CONF": "expandable_segments:True"
      }
    }
  ]
}
```

### 2. Start HTTP API Server

```bash
python comfyui_service_manager.py server --port 9999
```

### 3. Switch Services

```bash
# Using CLI
python comfyui_service_manager.py switch no-cache

# Using HTTP API
curl -X POST http://localhost:9999/switch/no-cache
```

## HTTP API

### Endpoints

| Method | Endpoint | Description |
|--------|-----------|-------------|
| GET | `/status` | Get status of all services |
| GET | `/services` | List all configured services |
| POST | `/switch/<service_name>` | Switch to a specific service |
| POST | `/start/<service_name>` | Start a specific service |
| POST | `/stop/<service_name>` | Stop a specific service |

### Example Usage

```bash
# Check status
curl http://localhost:9999/status

# Switch to no-cache service
curl -X POST http://localhost:9999/switch/no-cache

# Start normal service
curl -X POST http://localhost:9999/start/normal

# Stop no-cache service
curl -X POST http://localhost:9999/stop/no-cache
```

## CLI Usage

```bash
# Show all services status
python comfyui_service_manager.py status

# Start a service
python comfyui_service_manager.py start normal

# Stop a service
python comfyui_service_manager.py stop no-cache

# Switch services
python comfyui_service_manager.py switch normal

# Start HTTP API server
python comfyui_service_manager.py server --port 9999
```

## Configuration

### Service Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | string | Unique service identifier |
| `port` | int | Port number for ComfyUI |
| `python_executable` | string | Full path to Python interpreter |
| `listen_host` | string | Address to bind to (default: "0.0.0.0") |
| `vram_mode` | string | VRAM mode: `--normalvram`, `--highvram`, `--lowvram` |
| `extra_args` | array | Additional command-line arguments |
| `work_dir` | string | ComfyUI installation directory |
| `env_vars` | object | Environment variables to set |

## Integration with ComfyUI Workflow Runner

```python
from scripts import ComfyUIRunner, ComfyUIConfig

config = ComfyUIConfig(
    server_address="192.168.1.179:8188",
    service_manager_enabled=True,
    service_manager_address="localhost:9999"
)

runner = ComfyUIRunner(config)

# Automatically switch to no-cache service before video generation
runner.generate_video(
    image_path="input.jpg",
    prompt="camera pans",
    service_name="no-cache"
)
```

## How It Works

### Service Switching Process

1. **Stop Current Service**: Gracefully stops the currently active service
2. **Wait for Cleanup**: Waits 2 seconds for port to be released
3. **Start Target Service**: Launches the new service with its configuration
4. **PID Tracking**: Saves the new process ID to `pids/<service_name>.pid`

### Process Persistence

The service manager tracks PIDs across restarts:

```
pids/
├── normal.pid
└── no-cache.pid
```

When the service manager restarts:
- Reads PID files to restore service state
- Verifies processes are still running
- Cleans up stale PID files

## Common Use Cases

### Scenario 1: Switch Based on Task Type

```bash
# Use normal mode for simple tasks
python comfyui_service_manager.py switch normal

# Switch to no-cache for heavy tasks
python comfyui_service_manager.py switch no-cache
```

### Scenario 2: Memory Management

```bash
# Start with low memory mode
python comfyui_service_manager.py start lowvram

# Switch to high memory mode when needed
python comfyui_service_manager.py switch highvram
```

## Requirements

- Python 3.8+
- Flask (for HTTP API)
- ComfyUI installed and configured

## Development

```bash
# Clone repository
git clone https://github.com/yourusername/comfyui-service-manager.git
cd comfyui-service-manager

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run tests
python -m pytest tests/
```

## License

MIT License - see LICENSE file for details

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## Support

For issues and questions, please use the GitHub issue tracker.
