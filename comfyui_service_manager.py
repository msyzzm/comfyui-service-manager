#!/usr/bin/env python3
"""
ComfyUI Service Manager

Manages multiple ComfyUI services with different configurations.
Provides HTTP API to switch between services.

Usage:
    python comfyui_service_manager.py start <service>
    python comfyui_service_manager.py switch <service>
    python comfyui_service_manager.py server --port 9999
"""

import argparse
import atexit
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

try:
    from flask import Flask, jsonify, request, send_from_directory
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    print("Warning: Flask not installed. HTTP API will not be available.")
    print("Install with: pip install flask")

try:
    from flask_cors import CORS
    CORS_AVAILABLE = True
except ImportError:
    CORS_AVAILABLE = False


class ServiceStatus(Enum):
    """Service status enum"""
    STOPPED = "stopped"
    RUNNING = "running"
    STARTING = "starting"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class ComfyUIService:
    """ComfyUI service configuration"""
    name: str
    port: int
    python_executable: str = None
    listen_host: str = "0.0.0.0"
    vram_mode: str = "--normalvram"
    extra_args: List[str] = field(default_factory=list)
    work_dir: str = None
    env_vars: Dict[str, str] = field(default_factory=dict)
    process: Optional[subprocess.Popen] = None
    status: ServiceStatus = ServiceStatus.STOPPED
    pid: Optional[int] = None

    def __post_init__(self):
        if self.python_executable is None:
            self.python_executable = sys.executable
        if self.work_dir is None:
            self.work_dir = os.path.expanduser("~/ComfyUI")


class ServiceManager:
    """Manages multiple ComfyUI services"""

    # Max log file size before rotation (10 MB)
    MAX_LOG_SIZE = 10 * 1024 * 1024
    # Max number of rotated log files to keep
    MAX_LOG_FILES = 3

    def __init__(self, config_file: str = None):
        self.services: Dict[str, ComfyUIService] = {}
        self.config_file = config_file or self._get_default_config_path()
        self.active_service: Optional[str] = None
        self.http_port = 9999
        self.pid_dir = "pids"
        self.logs_dir = "logs"
        self._lock = threading.Lock()

        Path(self.pid_dir).mkdir(exist_ok=True)
        Path(self.logs_dir).mkdir(exist_ok=True)

        self.load_config()
        self._restore_service_state()

    def _rotate_log(self, service_name: str):
        """Rotate log file if it exceeds MAX_LOG_SIZE.

        Keeps at most MAX_LOG_FILES rotated copies:
          name.log → name.log.1 → name.log.2 → ... (oldest deleted)
        """
        log_file = Path(self.logs_dir) / f"{service_name}.log"
        if not log_file.exists():
            return

        try:
            if log_file.stat().st_size < self.MAX_LOG_SIZE:
                return
        except OSError:
            return

        # Delete the oldest rotated file
        oldest = Path(self.logs_dir) / f"{service_name}.log.{self.MAX_LOG_FILES}"
        if oldest.exists():
            oldest.unlink()

        # Shift existing rotated files: .2 → .3, .1 → .2
        for i in range(self.MAX_LOG_FILES, 1, -1):
            src = Path(self.logs_dir) / f"{service_name}.log.{i - 1}"
            dst = Path(self.logs_dir) / f"{service_name}.log.{i}"
            if src.exists():
                src.rename(dst)

        # Current → .1
        log_file.rename(Path(self.logs_dir) / f"{service_name}.log.1")
        print(f"[Log] Rotated log for '{service_name}'")

    def _get_default_config_path(self) -> str:
        config_path = Path("config/services.json")
        if config_path.exists():
            return str(config_path)
        return "services.json"

    def load_config(self):
        """Load service configuration from JSON file"""
        config_path = Path(self.config_file)
        if not config_path.exists():
            self.create_default_config()
            return

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error loading config: {e}")
            return

        self.http_port = config.get("http_port", 9999)
        for svc_config in config.get("services", []):
            try:
                # Filter out runtime-only fields that shouldn't be in config
                svc_config.pop("process", None)
                svc_config.pop("status", None)
                svc_config.pop("pid", None)
                service = ComfyUIService(**svc_config)
                self.services[service.name] = service
            except (TypeError, KeyError) as e:
                print(f"Warning: Skipping invalid service config: {e}")

    def auto_start_first_service(self) -> bool:
        """Automatically start the first service if no service is running."""
        if self.active_service:
            print(f"[Auto-start] Service '{self.active_service}' is already running")
            return False

        if not self.services:
            print("[Auto-start] No services configured")
            return False

        first_service_name = list(self.services.keys())[0]
        print(f"[Auto-start] Starting first service: '{first_service_name}'")

        success = self.start_service(first_service_name)
        if success:
            print(f"[Auto-start] Successfully started '{first_service_name}'")
        else:
            print(f"[Auto-start] Failed to start '{first_service_name}'")
        return success

    def create_default_config(self):
        """Create default service configuration"""
        self.services = {
            "normal": ComfyUIService(
                name="normal",
                port=8188,
                vram_mode="--normalvram",
                extra_args=["--listen"]
            ),
            "highvram": ComfyUIService(
                name="highvram",
                port=8189,
                vram_mode="--highvram",
                extra_args=["--listen"]
            ),
        }
        self.save_config()

    def save_config(self):
        """Save service configuration to JSON file"""
        config = {
            "http_port": self.http_port,
            "services": []
        }
        for service in self.services.values():
            config["services"].append({
                "name": service.name,
                "port": service.port,
                "python_executable": service.python_executable,
                "listen_host": service.listen_host,
                "vram_mode": service.vram_mode,
                "extra_args": service.extra_args,
                "work_dir": service.work_dir,
                "env_vars": service.env_vars
            })

        config_path = Path(self.config_file)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    def reload_config(self) -> bool:
        """Reload configuration from file, preserving running service states."""
        try:
            config_path = Path(self.config_file)
            if not config_path.exists():
                print(f"Config file not found: {self.config_file}")
                return False

            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)

            with self._lock:
                current_active = self.active_service
                old_services = self.services.copy()

                self.services = {}
                self.http_port = config.get("http_port", 9999)

                for svc_config in config.get("services", []):
                    svc_config.pop("process", None)
                    svc_config.pop("status", None)
                    svc_config.pop("pid", None)
                    try:
                        service = ComfyUIService(**svc_config)
                    except (TypeError, KeyError) as e:
                        print(f"Warning: Skipping invalid service config: {e}")
                        continue
                    if service.name in old_services:
                        old = old_services[service.name]
                        service.pid = old.pid
                        service.status = old.status
                        service.process = old.process
                    self.services[service.name] = service

                self.active_service = current_active

            print(f"Configuration reloaded from {self.config_file}")
            print(f"Services: {list(self.services.keys())}")
            return True

        except Exception as e:
            print(f"Error reloading config: {e}")
            return False

    def get_service(self, name: str) -> Optional[ComfyUIService]:
        return self.services.get(name)

    def _get_pid_file_path(self, service_name: str) -> str:
        return os.path.join(self.pid_dir, f"{service_name}.pid")

    def _save_pid(self, service_name: str, pid: int):
        pid_file = self._get_pid_file_path(service_name)
        with open(pid_file, "w") as f:
            f.write(str(pid))

    def _load_pid(self, service_name: str) -> Optional[int]:
        pid_file = self._get_pid_file_path(service_name)
        if os.path.exists(pid_file):
            try:
                with open(pid_file, "r") as f:
                    return int(f.read().strip())
            except (ValueError, IOError):
                pass
        return None

    def _delete_pid_file(self, service_name: str):
        pid_file = self._get_pid_file_path(service_name)
        if os.path.exists(pid_file):
            os.remove(pid_file)

    def _is_process_running(self, pid: int) -> bool:
        """Check if a process is still running"""
        try:
            if sys.platform == "win32":
                import ctypes
                kernel32 = ctypes.windll.kernel32
                SYNCHRONIZE = 0x100000
                handle = kernel32.OpenProcess(SYNCHRONIZE, 0, pid)
                if handle:
                    kernel32.CloseHandle(handle)
                    return True
                return False
            else:
                os.kill(pid, 0)
                return True
        except (OSError, ProcessLookupError):
            return False

    def _restore_service_state(self):
        """Restore service states from PID files."""
        restored_active = None
        for name, service in self.services.items():
            pid = self._load_pid(name)
            if pid and self._is_process_running(pid):
                service.pid = pid
                service.status = ServiceStatus.RUNNING
                if restored_active is None:
                    restored_active = name
                print(f"Restored service '{name}' (PID: {pid})")
            elif pid:
                self._delete_pid_file(name)

        # Only restore active if we didn't already have one
        if self.active_service is None:
            self.active_service = restored_active

    def start_service(self, name: str) -> bool:
        """Start a ComfyUI service"""
        with self._lock:
            service = self.get_service(name)
            if not service:
                print(f"Error: Service '{name}' not found")
                return False

            if service.status == ServiceStatus.RUNNING:
                print(f"Service '{name}' is already running")
                return True

            if service.status == ServiceStatus.STARTING:
                print(f"Service '{name}' is already starting")
                return False

        print(f"Starting service '{name}' on port {service.port}...")

        cmd = [
            service.python_executable,
            "main.py",
            service.vram_mode,
            "--port", str(service.port)
        ]
        cmd.extend(service.extra_args)

        env = os.environ.copy()
        env.update(service.env_vars)

        log_file = os.path.join(self.logs_dir, f"{name}.log")
        self._rotate_log(name)
        print(f"Service '{name}' log: {log_file}")

        try:
            log_handle = open(log_file, "a")

            process = subprocess.Popen(
                cmd,
                cwd=service.work_dir,
                env=env,
                stdout=log_handle,
                stderr=log_handle,
                preexec_fn=os.setsid if hasattr(os, 'setsid') else None
            )

            with self._lock:
                service.process = process
                service.pid = process.pid
                service.status = ServiceStatus.STARTING

            # Wait in background to check if process survives
            def _wait_for_startup():
                time.sleep(3)
                with self._lock:
                    if process.poll() is None:
                        service.status = ServiceStatus.RUNNING
                        self.active_service = name
                        self._save_pid(name, process.pid)
                        print(f"Service '{name}' started successfully (PID: {process.pid})")
                    else:
                        service.status = ServiceStatus.ERROR
                        print(f"Service '{name}' failed to start (exit code: {process.returncode})")

            t = threading.Thread(target=_wait_for_startup, daemon=True)
            t.start()
            return True

        except Exception as e:
            with self._lock:
                service.status = ServiceStatus.ERROR
            print(f"Error starting service '{name}': {e}")
            return False

    def stop_service(self, name: str) -> bool:
        """Stop a ComfyUI service"""
        with self._lock:
            service = self.get_service(name)
            if not service:
                print(f"Error: Service '{name}' not found")
                return False

            if service.status not in (ServiceStatus.RUNNING, ServiceStatus.STARTING, ServiceStatus.ERROR):
                print(f"Service '{name}' is not running")
                return True

            service.status = ServiceStatus.STOPPING

        print(f"Stopping service '{name}'...")

        try:
            target_pid = service.pid

            if not service.process and target_pid:
                if self._is_process_running(target_pid):
                    if sys.platform == "win32":
                        # Try graceful first, then force
                        subprocess.run(
                            ["taskkill", "/PID", str(target_pid)],
                            capture_output=True, timeout=5
                        )
                        time.sleep(2)
                        if self._is_process_running(target_pid):
                            subprocess.run(
                                ["taskkill", "/F", "/PID", str(target_pid)],
                                capture_output=True, timeout=5
                            )
                    else:
                        os.kill(target_pid, signal.SIGTERM)
                        time.sleep(2)
                        if self._is_process_running(target_pid):
                            os.kill(target_pid, signal.SIGKILL)
                else:
                    self._delete_pid_file(name)
                    with self._lock:
                        service.status = ServiceStatus.STOPPED
                        service.pid = None
                    return True

            elif service.process:
                if hasattr(os, 'getpgid'):
                    try:
                        process_group = os.getpgid(service.process.pid)
                        os.killpg(process_group, signal.SIGTERM)
                    except (OSError, ProcessLookupError):
                        service.process.terminate()
                else:
                    service.process.terminate()

                try:
                    service.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    if hasattr(os, 'getpgid'):
                        try:
                            os.killpg(process_group, signal.SIGKILL)
                        except (OSError, ProcessLookupError):
                            service.process.kill()
                    else:
                        service.process.kill()
                    service.process.wait()

            with self._lock:
                service.status = ServiceStatus.STOPPED
                service.process = None
                service.pid = None
                if self.active_service == name:
                    self.active_service = None
            self._delete_pid_file(name)
            print(f"Service '{name}' stopped successfully")
            return True

        except Exception as e:
            print(f"Error stopping service '{name}': {e}")
            with self._lock:
                service.status = ServiceStatus.ERROR
            return False

    def switch_service(self, target_name: str) -> bool:
        """Switch to a different service"""
        if target_name not in self.services:
            print(f"Error: Service '{target_name}' not found")
            return False

        if self.active_service and self.active_service != target_name:
            print(f"Stopping current service '{self.active_service}'...")
            if not self.stop_service(self.active_service):
                return False
            time.sleep(2)

        return self.start_service(target_name)

    def get_status(self) -> Dict:
        """Get status of all services"""
        with self._lock:
            services_status = {}
            for name, service in self.services.items():
                if service.process and service.process.poll() is not None:
                    service.status = ServiceStatus.STOPPED
                    service.process = None
                    service.pid = None

                # Build full startup command string
                startup_args = [service.vram_mode, "--port", str(service.port)] + service.extra_args

                services_status[name] = {
                    "status": service.status.value,
                    "port": service.port,
                    "pid": service.pid,
                    "vram_mode": service.vram_mode,
                    "extra_args": service.extra_args,
                    "startup_args": startup_args,
                    "is_active": name == self.active_service
                }

            return {
                "active_service": self.active_service,
                "services": services_status,
                "timestamp": datetime.now().isoformat()
            }

    def shutdown_all(self):
        """Stop all running services. Safe to call multiple times."""
        names_to_stop = []
        with self._lock:
            for name, service in self.services.items():
                if service.status in (ServiceStatus.RUNNING, ServiceStatus.STARTING):
                    names_to_stop.append(name)

        if not names_to_stop:
            return

        print(f"\n[Shutdown] Stopping {len(names_to_stop)} service(s): {names_to_stop}")
        for name in names_to_stop:
            try:
                self.stop_service(name)
            except Exception as e:
                print(f"[Shutdown] Error stopping '{name}': {e}")

    def get_service_logs(self, service_name: str, tail: int = 100) -> Optional[str]:
        """Get logs for a specific service. Reads from the end of the file efficiently."""
        log_file = os.path.join(self.logs_dir, f"{service_name}.log")
        if not os.path.exists(log_file):
            return None

        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                if not tail:
                    return f.read()

                # Efficient tail read: seek from end, avoid loading entire file
                f.seek(0, 2)
                file_size = f.tell()
                # Read a generous chunk: assume ~200 bytes per line
                read_size = min(tail * 200, file_size)
                f.seek(max(0, file_size - read_size))
                lines = f.readlines()
                return "".join(lines[-tail:])
        except Exception as e:
            return f"Error reading log: {e}"


# =============================================================================
# HTTP API Server
# =============================================================================

def create_http_api(manager: ServiceManager):
    """Create Flask HTTP API"""
    if not FLASK_AVAILABLE:
        return None

    app = Flask(__name__, static_folder="static", static_url_path="/static")

    if CORS_AVAILABLE:
        CORS(app)

    @app.route('/')
    def index():
        """Serve the frontend UI"""
        static_dir = Path(__file__).parent / "static"
        index_file = static_dir / "index.html"
        if index_file.exists():
            return send_from_directory(str(static_dir), "index.html")
        return jsonify({
            "message": "ComfyUI Service Manager API",
            "hint": "Place index.html in the 'static' folder to enable the web UI",
            "endpoints": {
                "GET /status": "Get all services status",
                "GET /services": "List configured services",
                "POST /switch/<name>": "Switch to a service",
                "POST /start/<name>": "Start a service",
                "POST /stop/<name>": "Stop a service",
                "GET /logs/<name>": "Get service logs",
                "POST /reload": "Reload configuration",
            }
        })

    @app.route('/status', methods=['GET'])
    def get_status():
        return jsonify(manager.get_status())

    @app.route('/services', methods=['GET'])
    def list_services():
        return jsonify({
            "services": list(manager.services.keys()),
            "active": manager.active_service
        })

    @app.route('/reload', methods=['POST'])
    def reload_config():
        result = manager.reload_config()
        return jsonify({
            "success": result,
            "services": list(manager.services.keys()),
            "message": "Configuration reloaded" if result else "Failed to reload configuration"
        })

    @app.route('/switch/<service_name>', methods=['POST'])
    def switch_service(service_name):
        result = manager.switch_service(service_name)
        return jsonify({
            "success": result,
            "active_service": manager.active_service,
            "message": f"Switched to {service_name}" if result else f"Failed to switch to {service_name}"
        })

    @app.route('/start/<service_name>', methods=['POST'])
    def start_service(service_name):
        result = manager.start_service(service_name)
        return jsonify({
            "success": result,
            "message": f"Started {service_name}" if result else f"Failed to start {service_name}"
        })

    @app.route('/stop/<service_name>', methods=['POST'])
    def stop_service(service_name):
        result = manager.stop_service(service_name)
        return jsonify({
            "success": result,
            "message": f"Stopped {service_name}" if result else f"Failed to stop {service_name}"
        })

    @app.route('/logs/<service_name>', methods=['GET'])
    def get_logs(service_name):
        tail = request.args.get('tail', default=100, type=int)
        log_content = manager.get_service_logs(service_name, tail=tail)
        if log_content is None:
            return jsonify({
                "error": f"Log file not found for service '{service_name}'"
            }), 404
        return jsonify({
            "service": service_name,
            "logs": log_content
        })

    return app


# =============================================================================
# CLI Interface
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="ComfyUI Service Manager - Manage multiple ComfyUI instances"
    )
    parser.add_argument(
        'command',
        choices=['start', 'stop', 'switch', 'status', 'logs', 'server'],
        help="Command to execute"
    )
    parser.add_argument(
        'service',
        nargs='?',
        help="Service name (for start/stop/switch/logs commands)"
    )
    parser.add_argument(
        '--config',
        default=None,
        help="Path to configuration file"
    )
    parser.add_argument(
        '--port',
        type=int,
        default=9999,
        help="HTTP API port (default: 9999)"
    )
    parser.add_argument(
        '--tail',
        type=int,
        default=100,
        help="Number of log lines to show (default: 100)"
    )
    parser.add_argument(
        '--follow',
        action='store_true',
        help="Follow log output (like tail -f)"
    )

    args = parser.parse_args()

    manager = ServiceManager(config_file=args.config)
    manager.http_port = args.port

    # Ensure child processes are killed on exit
    atexit.register(manager.shutdown_all)
    original_sigint = signal.getsignal(signal.SIGINT)
    original_sigterm = signal.getsignal(signal.SIGTERM)

    def _signal_handler(signum, _frame):
        manager.shutdown_all()
        # Restore original handler and re-raise so the default behavior applies
        signal.signal(signum, original_sigint if signum == signal.SIGINT else original_sigterm)
        signal.raise_signal(signum)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    if args.command == 'status':
        status = manager.get_status()
        print(f"Active Service: {status['active_service']}")
        print("\nServices:")
        for name, info in status['services'].items():
            active_mark = " [ACTIVE]" if info['is_active'] else ""
            print(f"  {name}: {info['status']} (port {info['port']}, PID: {info['pid']}){active_mark}")

    elif args.command == 'start':
        if not args.service:
            parser.error("start command requires a service name")
        success = manager.start_service(args.service)
        sys.exit(0 if success else 1)

    elif args.command == 'stop':
        if not args.service:
            parser.error("stop command requires a service name")
        success = manager.stop_service(args.service)
        sys.exit(0 if success else 1)

    elif args.command == 'switch':
        if not args.service:
            parser.error("switch command requires a service name")
        success = manager.switch_service(args.service)
        sys.exit(0 if success else 1)

    elif args.command == 'logs':
        if not args.service:
            parser.error("logs command requires a service name")
        log_file = os.path.join(manager.logs_dir, f"{args.service}.log")
        if not os.path.exists(log_file):
            print(f"Log file not found: {log_file}")
            sys.exit(1)

        if args.follow:
            print(f"Following logs for '{args.service}' (Ctrl+C to stop)...")
            print(f"Log file: {log_file}")
            print("-" * 50)
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)
                while True:
                    line = f.readline()
                    if line:
                        print(line, end="")
                    else:
                        time.sleep(0.1)
        else:
            logs = manager.get_service_logs(args.service, tail=args.tail)
            if logs:
                print(f"Logs for '{args.service}' (last {args.tail} lines):")
                print(f"Log file: {log_file}")
                print("-" * 50)
                print(logs)
            else:
                print(f"Log file is empty: {log_file}")

    elif args.command == 'server':
        if not FLASK_AVAILABLE:
            print("Error: Flask is required for HTTP API server")
            print("Install with: pip install flask")
            sys.exit(1)

        manager.auto_start_first_service()

        app = create_http_api(manager)
        print(f"Starting ComfyUI Service Manager on port {manager.http_port}...")
        print(f"Web UI: http://localhost:{manager.http_port}")
        print(f"API endpoints:")
        print(f"  GET  /status              - All services status")
        print(f"  GET  /services            - List services")
        print(f"  POST /switch/<name>       - Switch service")
        print(f"  POST /start/<name>        - Start service")
        print(f"  POST /stop/<name>         - Stop service")
        print(f"  GET  /logs/<name>         - Service logs")
        print(f"  POST /reload              - Reload config")
        app.run(host='0.0.0.0', port=manager.http_port, debug=False)


if __name__ == "__main__":
    main()
