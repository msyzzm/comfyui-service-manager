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
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

try:
    from flask import Flask, jsonify, request
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    print("Warning: Flask not installed. HTTP API will not be available.")
    print("Install with: pip install flask")


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
    python_executable: str = None  # Path to Python interpreter
    listen_host: str = "0.0.0.0"
    vram_mode: str = "--normalvram"  # --normalvram, --highvram, --lowvram
    extra_args: List[str] = field(default_factory=list)
    work_dir: str = None
    env_vars: Dict[str, str] = field(default_factory=dict)
    process: Optional[subprocess.Popen] = None
    status: ServiceStatus = ServiceStatus.STOPPED
    pid: Optional[int] = None

    def __post_init__(self):
        if self.python_executable is None:
            # Default to current Python
            self.python_executable = sys.executable
        if self.work_dir is None:
            # Default to ComfyUI directory
            self.work_dir = os.path.expanduser("~/ComfyUI")


class ServiceManager:
    """Manages multiple ComfyUI services"""

    def __init__(self, config_file: str = None):
        self.services: Dict[str, ComfyUIService] = {}
        self.config_file = config_file or self._get_default_config_path()
        self.active_service: Optional[str] = None
        self.http_port = 9999
        self.pid_dir = "pids"  # Directory to store PID files
        self.logs_dir = "logs"  # Directory to store service logs
        self.load_config()

        # Create directories
        Path(self.pid_dir).mkdir(exist_ok=True)
        Path(self.logs_dir).mkdir(exist_ok=True)

    def _get_default_config_path(self) -> str:
        """Get default config file path"""
        # Try config/services.json first, then services.json in current directory
        config_path = Path("config/services.json")
        if config_path.exists():
            return str(config_path)
        return "services.json"

    def load_config(self):
        """Load service configuration from JSON file"""
        config_path = Path(self.config_file)
        if config_path.exists():
            with open(config_path, "r") as f:
                config = json.load(f)
                self.http_port = config.get("http_port", 9999)
                for svc_config in config.get("services", []):
                    service = ComfyUIService(**svc_config)
                    self.services[service.name] = service
        else:
            # Create default configuration
            self.create_default_config()

        # Restore service state from PID files
        self._restore_service_state()

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
            service_dict = {
                "name": service.name,
                "port": service.port,
                "python_executable": service.python_executable,
                "listen_host": service.listen_host,
                "vram_mode": service.vram_mode,
                "extra_args": service.extra_args,
                "work_dir": service.work_dir,
                "env_vars": service.env_vars
            }
            config["services"].append(service_dict)

        with open(self.config_file, "w") as f:
            json.dump(config, f, indent=2)

    def reload_config(self) -> bool:
        """
        Reload configuration from file.

        This preserves the active service and running processes,
        but updates service definitions from the config file.

        Returns:
            True if reload was successful
        """
        try:
            config_path = Path(self.config_file)
            if not config_path.exists():
                print(f"Config file not found: {self.config_file}")
                return False

            with open(config_path, "r") as f:
                config = json.load(f)

            # Store current active service
            current_active = self.active_service

            # Store current service states (PIDs, statuses)
            old_services = self.services.copy()

            # Update services from config
            self.services = {}
            self.http_port = config.get("http_port", 9999)

            for svc_config in config.get("services", []):
                service = ComfyUIService(**svc_config)
                # Restore state if service existed before
                if service.name in old_services:
                    old_service = old_services[service.name]
                    service.pid = old_service.pid
                    service.status = old_service.status
                    service.process = old_service.process
                self.services[service.name] = service

            # Restore active service
            self.active_service = current_active

            print(f"Configuration reloaded from {self.config_file}")
            print(f"Services: {list(self.services.keys())}")
            return True

        except Exception as e:
            print(f"Error reloading config: {e}")
            return False

    def get_service(self, name: str) -> Optional[ComfyUIService]:
        """Get service by name"""
        return self.services.get(name)

    def _get_pid_file_path(self, service_name: str) -> str:
        """Get PID file path for a service"""
        return os.path.join(self.pid_dir, f"{service_name}.pid")

    def _save_pid(self, service_name: str, pid: int):
        """Save PID to file"""
        pid_file = self._get_pid_file_path(service_name)
        with open(pid_file, "w") as f:
            f.write(str(pid))

    def _load_pid(self, service_name: str) -> Optional[int]:
        """Load PID from file"""
        pid_file = self._get_pid_file_path(service_name)
        if os.path.exists(pid_file):
            try:
                with open(pid_file, "r") as f:
                    return int(f.read().strip())
            except (ValueError, IOError):
                pass
        return None

    def _delete_pid_file(self, service_name: str):
        """Delete PID file for a service"""
        pid_file = self._get_pid_file_path(service_name)
        if os.path.exists(pid_file):
            os.remove(pid_file)

    def _is_process_running(self, pid: int) -> bool:
        """Check if a process is still running"""
        try:
            if sys.platform == "win32":
                import ctypes
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(1, 0, pid)
                if handle:
                    kernel32.CloseHandle(handle)
                    return True
                return False
            else:
                os.kill(pid, 0)  # Send signal 0 to check if process exists
                return True
        except (OSError, ProcessLookupError):
            return False

    def _restore_service_state(self):
        """Restore service states from PID files and running processes"""
        for name, service in self.services.items():
            # Try to load PID from file
            pid = self._load_pid(name)

            if pid:
                # Check if process is still running
                if self._is_process_running(pid):
                    service.pid = pid
                    service.status = ServiceStatus.RUNNING
                    if self.active_service is None:
                        self.active_service = name
                    print(f"Restored service '{name}' (PID: {pid})")
                else:
                    # PID file exists but process not running, clean up
                    self._delete_pid_file(name)

    def start_service(self, name: str) -> bool:
        """Start a ComfyUI service"""
        service = self.get_service(name)
        if not service:
            print(f"Error: Service '{name}' not found")
            return False

        if service.status == ServiceStatus.RUNNING:
            print(f"Service '{name}' is already running")
            return True

        print(f"Starting service '{name}' on port {service.port}...")

        # Build command
        cmd = [
            service.python_executable,  # Use configured Python executable
            "main.py",
            service.vram_mode,
            "--port", str(service.port)
        ]
        cmd.extend(service.extra_args)

        # Prepare environment
        env = os.environ.copy()
        env.update(service.env_vars)

        # Prepare log file
        log_file = os.path.join(self.logs_dir, f"{name}.log")
        print(f"Service '{name}' log: {log_file}")

        try:
            # Open log file for writing
            log_handle = open(log_file, "a")

            # Start process with output redirected to log file
            process = subprocess.Popen(
                cmd,
                cwd=service.work_dir,
                env=env,
                stdout=log_handle,
                stderr=log_handle,
                preexec_fn=os.setsid if hasattr(os, 'setsid') else None
            )

            service.process = process
            service.pid = process.pid
            service.status = ServiceStatus.STARTING

            # Wait a bit to check if it started successfully
            time.sleep(3)
            if process.poll() is None:
                service.status = ServiceStatus.RUNNING
                service.pid = process.pid
                print(f"Service '{name}' started successfully (PID: {process.pid})")
                self.active_service = name
                # Save PID to file
                self._save_pid(name, process.pid)
                return True
            else:
                service.status = ServiceStatus.ERROR
                print(f"Service '{name}' failed to start")
                return False

        except Exception as e:
            service.status = ServiceStatus.ERROR
            print(f"Error starting service '{name}': {e}")
            return False

    def stop_service(self, name: str) -> bool:
        """Stop a ComfyUI service"""
        service = self.get_service(name)
        if not service:
            print(f"Error: Service '{name}' not found")
            return False

        if service.status != ServiceStatus.RUNNING:
            print(f"Service '{name}' is not running")
            return True

        print(f"Stopping service '{name}'...")

        try:
            target_pid = service.pid

            # If we don't have a process object but have a PID, try to use it
            if not service.process and target_pid:
                # Verify PID is still running
                if self._is_process_running(target_pid):
                    # Kill the process by PID
                    if sys.platform == "win32":
                        subprocess.run(["taskkill", "/F", "/PID", str(target_pid)],
                                      capture_output=True)
                    else:
                        os.kill(target_pid, signal.SIGTERM)
                        # Wait a bit for graceful shutdown
                        time.sleep(2)
                        # Force kill if still running
                        if self._is_process_running(target_pid):
                            os.kill(target_pid, signal.SIGKILL)
                else:
                    # PID not running, clean up
                    self._delete_pid_file(name)
                    service.status = ServiceStatus.STOPPED
                    service.pid = None
                    return True

            elif service.process:
                # Try graceful shutdown first
                process_group = os.getpgid(service.process.pid) if hasattr(os, 'getpgid') else None
                if process_group:
                    os.killpg(process_group, signal.SIGTERM)
                else:
                    service.process.terminate()

                # Wait for graceful shutdown
                try:
                    service.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    # Force kill if it doesn't stop
                    if process_group:
                        os.killpg(process_group, signal.SIGKILL)
                    else:
                        service.process.kill()
                    service.process.wait()

            service.status = ServiceStatus.STOPPED
            service.process = None
            service.pid = None
            # Delete PID file
            self._delete_pid_file(name)
            print(f"Service '{name}' stopped successfully")
            return True

        except Exception as e:
            print(f"Error stopping service '{name}': {e}")
            return False

    def switch_service(self, target_name: str) -> bool:
        """Switch to a different service"""
        if target_name not in self.services:
            print(f"Error: Service '{target_name}' not found")
            return False

        # Stop current active service
        if self.active_service and self.active_service != target_name:
            print(f"Stopping current service '{self.active_service}'...")
            if not self.stop_service(self.active_service):
                return False
            # Wait for service to fully stop
            time.sleep(2)

        # Start target service
        return self.start_service(target_name)

    def get_status(self) -> Dict:
        """Get status of all services"""
        services_status = {}
        for name, service in self.services.items():
            # Update process status
            if service.process and service.process.poll() is not None:
                service.status = ServiceStatus.STOPPED
                service.process = None
                service.pid = None

            services_status[name] = {
                "status": service.status.value,
                "port": service.port,
                "pid": service.pid,
                "is_active": name == self.active_service
            }

        return {
            "active_service": self.active_service,
            "services": services_status,
            "timestamp": datetime.now().isoformat()
        }

    def get_service_logs(self, service_name: str, tail: int = 100) -> Optional[str]:
        """
        Get logs for a specific service.

        Args:
            service_name: Name of the service
            tail: Number of lines to get from the end of the log file

        Returns:
            Log content as string, or None if log file not found
        """
        log_file = os.path.join(self.logs_dir, f"{service_name}.log")
        if not os.path.exists(log_file):
            return None

        try:
            with open(log_file, "r") as f:
                # Read last N lines
                lines = f.readlines()
                if tail:
                    lines = lines[-tail:]
                return "".join(lines)
        except Exception as e:
            return f"Error reading log: {e}"


# =============================================================================
# HTTP API Server
# =============================================================================

def create_http_api(manager: ServiceManager):
    """Create Flask HTTP API"""
    if not FLASK_AVAILABLE:
        return None

    app = Flask(__name__)

    @app.route('/status', methods=['GET'])
    def get_status():
        """Get status of all services"""
        return jsonify(manager.get_status())

    @app.route('/services', methods=['GET'])
    def list_services():
        """List all available services"""
        return jsonify({
            "services": list(manager.services.keys()),
            "active": manager.active_service
        })

    @app.route('/reload', methods=['POST'])
    def reload_config():
        """Reload configuration from file"""
        result = manager.reload_config()
        return jsonify({
            "success": result,
            "services": list(manager.services.keys()),
            "message": "Configuration reloaded" if result else "Failed to reload configuration"
        })

    @app.route('/switch/<service_name>', methods=['POST', 'GET'])
    def switch_service(service_name):
        """Switch to a specific service"""
        result = manager.switch_service(service_name)
        return jsonify({
            "success": result,
            "active_service": manager.active_service,
            "message": f"Switched to {service_name}" if result else f"Failed to switch to {service_name}"
        })

    @app.route('/start/<service_name>', methods=['POST'])
    def start_service(service_name):
        """Start a specific service"""
        result = manager.start_service(service_name)
        return jsonify({
            "success": result,
            "message": f"Started {service_name}" if result else f"Failed to start {service_name}"
        })

    @app.route('/stop/<service_name>', methods=['POST'])
    def stop_service(service_name):
        """Stop a specific service"""
        result = manager.stop_service(service_name)
        return jsonify({
            "success": result,
            "message": f"Stopped {service_name}" if result else f"Failed to stop {service_name}"
        })

    @app.route('/logs/<service_name>', methods=['GET'])
    def get_logs(service_name):
        """Get logs for a specific service"""
        tail = request.args.get('tail', default=100, type=int)
        log_content = manager.get_service_logs(service_name, tail=tail)
        if log_content is None:
            return jsonify({
                "error": f"Service '{service_name}' not found or log file not found"
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
    """Main CLI interface"""
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
        help="Path to configuration file (default: config/services.json or services.json)"
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

    # Create manager
    manager = ServiceManager(config_file=args.config)
    manager.http_port = args.port

    if args.command == 'status':
        # Show status
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
            # Follow mode (like tail -f)
            import time
            print(f"Following logs for '{args.service}' (Ctrl+C to stop)...")
            print(f"Log file: {log_file}")
            print("-" * 50)
            with open(log_file, "r") as f:
                # Go to end of file
                f.seek(0, 2)
                while True:
                    line = f.readline()
                    if line:
                        print(line, end="")
                    else:
                        time.sleep(0.1)
        else:
            # Show tail lines
            logs = manager.get_service_logs(args.service, tail=args.tail)
            if logs:
                print(f"Logs for '{args.service}' (last {args.tail} lines):")
                print(f"Log file: {log_file}")
                print("-" * 50)
                print(logs)
            else:
                print(f"Log file is empty: {log_file}")

    elif args.command == 'server':
        # Start HTTP API server
        if not FLASK_AVAILABLE:
            print("Error: Flask is required for HTTP API server")
            print("Install with: pip install flask")
            sys.exit(1)

        app = create_http_api(manager)
        print(f"Starting ComfyUI Service Manager HTTP API on port {manager.http_port}...")
        print(f"Available endpoints:")
        print(f"  GET  http://localhost:{manager.http_port}/status")
        print(f"  GET  http://localhost:{manager.http_port}/services")
        print(f"  POST http://localhost:{manager.http_port}/switch/<service_name>")
        print(f"  POST http://localhost:{manager.http_port}/start/<service_name>")
        print(f"  POST http://localhost:{manager.http_port}/stop/<service_name>")
        app.run(host='0.0.0.0', port=manager.http_port, debug=False)


if __name__ == "__main__":
    main()
