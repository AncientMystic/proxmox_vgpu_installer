import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
try:
    import paramiko
except ImportError:
    import sys
    import tkinter.messagebox as _mb
    _root = tk.Tk()
    _root.withdraw()
    _mb.showerror(
        "Missing dependency",
        "The 'paramiko' package is required.\n\nInstall it with:\n  pip install paramiko"
    )
    sys.exit(1)
import os
import re
import shlex
import threading
import queue
import time
import traceback
from pathlib import Path


class ProxmoxVGPUInstallerGUI:
    PATCH_BASE_URL = "https://gitlab.com/polloloco/vgpu-proxmox/-/raw/master/"

    def __init__(self, root):
        self.root = root
        self.root.title("Proxmox NVIDIA vGPU Installer (root-only)")
        self.root.geometry("900x700")

        # SSH client (only touched by worker thread, except snapshot in main thread)
        self.ssh = None
        self.sftp = None
        self._ssh_lock = threading.Lock()
        self._running = False

        # Queue for thread-safe logging
        self.log_queue = queue.Queue()

        # Variables (main thread only)
        self.host_var = tk.StringVar()
        self.user_var = tk.StringVar(value="root")
        self.password_var = tk.StringVar()
        # NOTE: StringVar on purpose. IntVar crashes the Entry when the
        # field is cleared or contains non-digits (TclError).
        self.port_var = tk.StringVar(value="22")

        self.driver_path_var = tk.StringVar()
        self.patch_dir_var = tk.StringVar(value="./patches")
        self.auto_download_patch_var = tk.BooleanVar(value=True)

        self.gpu_type_var = tk.StringVar(value="consumer")  # consumer, supported, pascal
        self.cpu_vendor_var = tk.StringVar(value="intel")   # intel, amd

        self.step_vars = {
            "install_prereq": tk.BooleanVar(value=True),
            "clone_build_unlock": tk.BooleanVar(value=True),
            "configure_unlock": tk.BooleanVar(value=True),
            "enable_iommu": tk.BooleanVar(value=True),
            "load_modules": tk.BooleanVar(value=True),
            "patch_driver": tk.BooleanVar(value=True),
            "install_driver": tk.BooleanVar(value=True),
            "reboot_after": tk.BooleanVar(value=False),
            "verify": tk.BooleanVar(value=True),
        }

        self.build_gui()

        # Periodically check for log messages
        self.root.after(100, self.process_log_queue)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def build_gui(self):
        # Main container
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # Connection frame
        conn_frame = ttk.LabelFrame(main_frame, text="SSH Connection (Proxmox root only, no sudo)", padding="5")
        conn_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=5)

        ttk.Label(conn_frame, text="Host:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Entry(conn_frame, textvariable=self.host_var, width=30).grid(row=0, column=1, padx=5, pady=2)

        ttk.Label(conn_frame, text="Username:").grid(row=0, column=2, sticky=tk.W, padx=5, pady=2)
        ttk.Entry(conn_frame, textvariable=self.user_var, width=15).grid(row=0, column=3, padx=5, pady=2)

        ttk.Label(conn_frame, text="Password:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Entry(conn_frame, textvariable=self.password_var, width=30, show="*").grid(row=1, column=1, padx=5, pady=2)

        ttk.Label(conn_frame, text="Port:").grid(row=1, column=2, sticky=tk.W, padx=5, pady=2)
        ttk.Entry(conn_frame, textvariable=self.port_var, width=8).grid(row=1, column=3, padx=5, pady=2)

        # Test connection button
        self.test_btn = ttk.Button(conn_frame, text="Test Connection", command=self.test_connection)
        self.test_btn.grid(row=2, column=0, columnspan=4, pady=5)

        # File selection frame
        file_frame = ttk.LabelFrame(main_frame, text="Driver and Patch", padding="5")
        file_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=5)

        ttk.Label(file_frame, text="Driver .run file:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Entry(file_frame, textvariable=self.driver_path_var, width=60).grid(row=0, column=1, padx=5, pady=2)
        ttk.Button(file_frame, text="Browse...", command=self.browse_driver).grid(row=0, column=2, padx=5, pady=2)

        ttk.Label(file_frame, text="Patch folder:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Entry(file_frame, textvariable=self.patch_dir_var, width=60).grid(row=1, column=1, padx=5, pady=2)
        ttk.Button(file_frame, text="Browse...", command=self.browse_patch_dir).grid(row=1, column=2, padx=5, pady=2)

        ttk.Checkbutton(file_frame, text="Auto-download patch if not found locally", variable=self.auto_download_patch_var).grid(row=2, column=0, columnspan=3, sticky=tk.W, padx=5, pady=2)

        # Options frame
        opt_frame = ttk.LabelFrame(main_frame, text="Options", padding="5")
        opt_frame.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=5)

        # GPU type and CPU vendor
        ttk.Label(opt_frame, text="GPU Type:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        gpu_combo = ttk.Combobox(opt_frame, textvariable=self.gpu_type_var, values=["consumer", "supported", "pascal"], state="readonly", width=15)
        gpu_combo.grid(row=0, column=1, padx=5, pady=2)

        ttk.Label(opt_frame, text="CPU Vendor:").grid(row=0, column=2, sticky=tk.W, padx=5, pady=2)
        cpu_combo = ttk.Combobox(opt_frame, textvariable=self.cpu_vendor_var, values=["intel", "amd"], state="readonly", width=10)
        cpu_combo.grid(row=0, column=3, padx=5, pady=2)

        # Steps checkboxes
        steps = [
            ("install_prereq", "Install prerequisite packages (apt update/upgrade + git, build-essential, dkms, pve-headers, mdevctl)"),
            ("clone_build_unlock", "Clone and build vgpu_unlock-rs"),
            ("configure_unlock", "Configure vGPU unlock (create config and systemd overrides)"),
            ("enable_iommu", "Enable IOMMU (edit GRUB/cmdline and update)"),
            ("load_modules", "Load kernel modules (vfio) and blacklist nouveau"),
            ("patch_driver", "Patch the NVIDIA driver (for consumer GPUs)"),
            ("install_driver", "Install the NVIDIA driver"),
            ("reboot_after", "Reboot after installation (if selected)"),
            ("verify", "Verify installation (nvidia-smi, mdevctl types, nvidia-smi vgpu)"),
        ]

        for i, (key, label) in enumerate(steps):
            ttk.Checkbutton(opt_frame, text=label, variable=self.step_vars[key]).grid(row=i+1, column=0, columnspan=4, sticky=tk.W, padx=5, pady=1)

        # Run and clear buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=3, column=0, pady=10)

        self.run_btn = ttk.Button(btn_frame, text="Run Selected Steps", command=self.run_steps)
        self.run_btn.pack(side=tk.LEFT, padx=5)

        ttk.Button(btn_frame, text="Clear Log", command=self.clear_log).pack(side=tk.LEFT, padx=5)

        # Log area
        log_frame = ttk.LabelFrame(main_frame, text="Log", padding="5")
        log_frame.grid(row=4, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        main_frame.rowconfigure(4, weight=1)

        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, width=100, height=20, state='disabled')
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def on_close(self):
        try:
            self.disconnect_ssh()
        finally:
            self.root.destroy()

    def browse_driver(self):
        filename = filedialog.askopenfilename(title="Select NVIDIA driver .run file", filetypes=[("NVIDIA driver", "*.run"), ("All files", "*.*")])
        if filename:
            self.driver_path_var.set(filename)

    def browse_patch_dir(self):
        dirname = filedialog.askdirectory(title="Select patch folder")
        if dirname:
            self.patch_dir_var.set(dirname)

    def log(self, message):
        """Add a message to the log queue (safe from any thread)."""
        self.log_queue.put(str(message))

    def process_log_queue(self):
        """Process pending log messages and update the text widget (main thread only)."""
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.configure(state='normal')
                self.log_text.insert(tk.END, msg + "\n")
                self.log_text.see(tk.END)
                self.log_text.configure(state='disabled')
        except queue.Empty:
            pass
        self.root.after(100, self.process_log_queue)

    def clear_log(self):
        self.log_text.configure(state='normal')
        self.log_text.delete(1.0, tk.END)
        self.log_text.configure(state='disabled')

    # ------------------------------------------------------------------
    # SSH helpers (root-only, no sudo)
    # ------------------------------------------------------------------
    def is_connected(self):
        ssh = self.ssh
        try:
            t = ssh.get_transport() if ssh is not None else None
            return t is not None and t.is_active()
        except Exception:
            return False

    def disconnect_ssh(self):
        with self._ssh_lock:
            # Close SFTP first, then SSH. Ignore errors, clear references.
            if self.sftp is not None:
                try:
                    self.sftp.close()
                except Exception:
                    pass
                self.sftp = None
            if self.ssh is not None:
                try:
                    self.ssh.close()
                except Exception:
                    pass
                self.ssh = None

    def connect_ssh(self, host, user, password, port):
        """Establish SSH connection. Caller must have snapshotted params (no Tk access here)."""
        with self._ssh_lock:
            # Close any stale session first to avoid leaks.
            if self.sftp is not None:
                try:
                    self.sftp.close()
                except Exception:
                    pass
                self.sftp = None
            if self.ssh is not None:
                try:
                    self.ssh.close()
                except Exception:
                    pass
                self.ssh = None

            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                ssh.connect(
                    hostname=host,
                    port=port,
                    username=user,
                    password=password,
                    timeout=10,
                    banner_timeout=10,
                    auth_timeout=10,
                )
                sftp = ssh.open_sftp()
            except Exception as e:
                try:
                    ssh.close()
                except Exception:
                    pass
                self.ssh = None
                self.sftp = None
                self.log(f"SSH connection failed: {e}")
                return False
            self.ssh = ssh
            self.sftp = sftp
            return True

    def test_connection(self):
        """Test SSH with an isolated client so a running install is never disturbed."""
        if self._running:
            messagebox.showinfo("Busy", "An installation run is in progress.")
            return
        # Snapshot + validate in main thread.
        host, user, password, port, err = self._snapshot_connection()
        if err:
            messagebox.showerror("Error", err)
            return

        self.test_btn.config(state='disabled')

        def _test():
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            sftp = None
            try:
                ssh.connect(hostname=host, port=port, username=user,
                            password=password, timeout=10,
                            banner_timeout=10, auth_timeout=10)
                sftp = ssh.open_sftp()
                # Cheap proof the session works + we are root.
                _in, out, _err = ssh.exec_command("whoami && uname -r", timeout=15)
                rc = out.channel.recv_exit_status()
                txt = (out.read().decode(errors="replace") or "").strip()
                if rc == 0:
                    self.log(f"SSH connection successful. Remote says:\n{txt}")
                else:
                    self.log(f"SSH connected but probe failed (rc={rc}).")
            except Exception as e:
                self.log(f"SSH connection test failed: {e}")
            finally:
                if sftp is not None:
                    try:
                        sftp.close()
                    except Exception:
                        pass
                try:
                    ssh.close()
                except Exception:
                    pass
                self.root.after(0, lambda: self.test_btn.config(state='normal'))

        threading.Thread(target=_test, daemon=True).start()

    # ------------------------------------------------------------------
    # Validation / snapshot (main thread only)
    # ------------------------------------------------------------------
    def _snapshot_connection(self):
        host = self.host_var.get().strip()
        user = self.user_var.get().strip()
        password = self.password_var.get()
        port_raw = self.port_var.get().strip()
        if not host:
            return None, None, None, None, "Host is required."
        if user != "root":
            return None, None, None, None, "This tool is root-only (Proxmox has no sudo). Username must be 'root'."
        if not password:
            return None, None, None, None, "Password is required."
        try:
            port = int(port_raw)
        except ValueError:
            return None, None, None, None, f"Port must be a number 1-65535 (got {port_raw!r})."
        if not (1 <= port <= 65535):
            return None, None, None, None, "Port must be in range 1-65535."
        return host, user, password, port, None

    def _snapshot_config(self):
        host, user, password, port, err = self._snapshot_connection()
        if err:
            return None, err
        steps = {k: v.get() for k, v in self.step_vars.items()}
        driver = self.driver_path_var.get().strip()
        patch_dir = self.patch_dir_var.get().strip() or "./patches"
        cfg = {
            "host": host, "user": user, "password": password, "port": port,
            "driver": driver, "patch_dir": patch_dir,
            "auto_download": bool(self.auto_download_patch_var.get()),
            "gpu_type": self.gpu_type_var.get(),
            "cpu_vendor": self.cpu_vendor_var.get(),
            "steps": steps,
        }
        if steps["patch_driver"] or steps["install_driver"]:
            if not driver:
                return None, "Driver file is required for patch/install steps."
            if not os.path.isfile(driver):
                return None, f"Driver file not found:\n{driver}"
            if os.path.getsize(driver) < 1024 * 1024:
                return None, f"Driver file looks too small (<1MB), refusing to upload:\n{driver}"
            ver = self.get_driver_version(os.path.basename(driver))
            if not ver:
                return None, (
                    "Could not determine driver version from filename.\n"
                    f"Got: {os.path.basename(driver)}\n"
                    "Expected like: NVIDIA-Linux-x86_64-550.144.02-vgpu-kvm.run\n"
                    "or: NVIDIA-Linux-x86_64-550.144.02.run"
                )
            cfg["driver_version"] = ver
        if steps["reboot_after"] and steps["verify"]:
            # Verify would run against a rebooting host; handle by skipping.
            pass
        return cfg, None

    # ------------------------------------------------------------------
    # Remote execution
    # ------------------------------------------------------------------
    def run_remote_command(self, command, timeout=120):
        """Execute a command as root and stream output. Returns True on rc==0."""
        ssh = self.ssh
        if ssh is None:
            self.log("ERROR: SSH not connected.")
            return False
        try:
            t = ssh.get_transport()
            if t is None or not t.is_active():
                self.log("ERROR: SSH transport is not active (rebooted/disconnected?).")
                return False
        except Exception as e:
            self.log(f"ERROR: SSH state check failed: {e}")
            return False

        # Never log secrets: there is no sudo wrapper anymore, command has no password.
        self.log(f"$ {command}")
        try:
            stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
            try:
                stdin.close()
            except Exception:
                pass
            channel = stdout.channel
            deadline = time.monotonic() + (timeout if timeout else 3600)
            # Stream available data until the remote side reports exit.
            while not channel.exit_status_ready():
                if time.monotonic() > deadline:
                    try:
                        channel.close()
                    except Exception:
                        pass
                    self.log(f"Command timed out after {timeout}s: {command}")
                    return False
                while channel.recv_ready():
                    try:
                        data = channel.recv(65536).decode(errors="replace")
                    except Exception:
                        break
                    for line in data.splitlines():
                        if line.strip():
                            self.log(line.rstrip())
                while channel.recv_stderr_ready():
                    try:
                        data = channel.recv_stderr(65536).decode(errors="replace")
                    except Exception:
                        break
                    for line in data.splitlines():
                        if line.strip():
                            self.log(f"STDERR: {line.rstrip()}")
                time.sleep(0.05)
            # Drain anything left after exit.
            try:
                while channel.recv_ready():
                    data = channel.recv(65536).decode(errors="replace")
                    for line in data.splitlines():
                        if line.strip():
                            self.log(line.rstrip())
                    if not data:
                        break
            except Exception:
                pass
            try:
                while channel.recv_stderr_ready():
                    data = channel.recv_stderr(65536).decode(errors="replace")
                    for line in data.splitlines():
                        if line.strip():
                            self.log(f"STDERR: {line.rstrip()}")
                    if not data:
                        break
            except Exception:
                pass
            try:
                exit_status = channel.recv_exit_status()
            except Exception as e:
                self.log(f"Could not get exit status: {e}")
                return False
            if exit_status != 0:
                self.log(f"Command failed with exit code {exit_status}")
                return False
            return True
        except Exception as e:
            self.log(f"Command execution error: {e}\n{traceback.format_exc(limit=3)}")
            return False

    def remote_file_exists(self, remote_path):
        """Quiet existence check (no scary error log for expected non-zero)."""
        ssh = self.ssh
        if ssh is None:
            return False
        try:
            t = ssh.get_transport()
            if t is None or not t.is_active():
                return False
            _in, out, _e = ssh.exec_command(f"test -f {shlex.quote(remote_path)}", timeout=15)
            return out.channel.recv_exit_status() == 0
        except Exception:
            return False

    def upload_file(self, local_path, remote_path):
        """Upload a file via SFTP."""
        if not os.path.isfile(local_path):
            self.log(f"Upload failed: local file not found: {local_path}")
            return False
        if self.sftp is None or not self.is_connected():
            self.log("Upload failed: SFTP not connected.")
            return False
        try:
            size = os.path.getsize(local_path)
            self.log(f"Uploading {local_path} ({size/1024/1024:.1f} MB) -> {remote_path} ...")
            self.sftp.put(local_path, remote_path)
            self.log(f"Uploaded {local_path} -> {remote_path}")
            return True
        except FileNotFoundError as e:
            self.log(f"Upload failed (path not found): {e}")
            return False
        except PermissionError as e:
            self.log(f"Upload failed (permission denied): {e}")
            return False
        except OSError as e:
            # Includes disk-full / network errors with errno preserved.
            self.log(f"Upload failed (OS error {getattr(e, 'errno', '?')}): {e}")
            return False
        except Exception as e:
            self.log(f"Upload failed: {e}")
            return False

    def download_patch(self, version, local_dir):
        """Download patch from GitLab (atomic write + basic validation)."""
        patch_filename = f"{version}.patch"
        url = self.PATCH_BASE_URL + patch_filename
        try:
            os.makedirs(local_dir, exist_ok=True)
        except Exception as e:
            self.log(f"Cannot create patch dir {local_dir}: {e}")
            return None
        local_path = os.path.join(local_dir, patch_filename)

        if os.path.exists(local_path):
            try:
                if os.path.getsize(local_path) > 0:
                    self.log(f"Patch already exists locally: {local_path}")
                    return local_path
                else:
                    self.log(f"Existing patch is empty, re-downloading: {local_path}")
                    os.remove(local_path)
            except Exception:
                pass

        self.log(f"Downloading patch from {url}")
        import urllib.request
        import urllib.error
        tmp_path = local_path + ".tmp"
        try:
            urllib.request.urlretrieve(url, tmp_path)
            # Basic integrity: non-empty and looks like a diff.
            size = os.path.getsize(tmp_path)
            if size == 0:
                raise ValueError("downloaded file is empty")
            with open(tmp_path, "r", errors="replace") as f:
                head = f.read(4096)
            if ("diff " not in head and "--- " not in head and "+++" not in head
                    and "From " not in head):
                # GitLab error pages are HTML.
                if "<html" in head.lower():
                    raise ValueError("server returned HTML (404/blocked?) instead of a patch")
                self.log("WARNING: downloaded file does not look like a patch; keeping it but patch may fail.")
            os.replace(tmp_path, local_path)
            self.log(f"Downloaded patch to {local_path} ({size} bytes)")
            return local_path
        except urllib.error.HTTPError as e:
            self.log(f"Failed to download patch: HTTP {e.code} {e.reason} for {url}")
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            return None
        except Exception as e:
            self.log(f"Failed to download patch: {e}")
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            return None

    def get_driver_version(self, driver_filename):
        """Extract version. Accepts -vgpu-kvm, grid, tesla, and plain consumer names."""
        base = os.path.basename(driver_filename)
        # Preferred: NVIDIA-Linux-x86_64-550.144.02-vgpu-kvm.run
        m = re.search(r'NVIDIA-Linux-x86_64-(\d+\.\d+(?:\.\d+)?)-vgpu-kvm\.run', base)
        if m:
            return m.group(1)
        # Fallback: NVIDIA-Linux-x86_64-550.144.02.run (and similar suffixes)
        m = re.search(r'NVIDIA-Linux-x86_64-(\d+\.\d+(?:\.\d+)?)(?:-[A-Za-z0-9_.-]+)?\.run', base)
        if m:
            return m.group(1)
        return None

    def run_steps(self):
        """Execute selected steps in a separate thread (snapshot Tk state first)."""
        if self._running:
            return

        cfg, err = self._snapshot_config()
        if err:
            messagebox.showerror("Error", err)
            return

        # Disable buttons synchronously (main thread) to prevent double-run.
        self._running = True
        self.run_btn.config(state='disabled')
        self.test_btn.config(state='disabled')
        self.clear_log()
        self.log("Starting installation process as root...")

        def _run():
            remote_driver = None
            version = cfg.get("driver_version")
            install_driver_file = None
            try:
                if not self.connect_ssh(cfg["host"], cfg["user"], cfg["password"], cfg["port"]):
                    self.log("Aborting due to SSH connection failure.")
                    return

                steps = cfg["steps"]

                # Pre-flight notes straight from the guide + live host checks.
                # These never abort (informational), except passthrough leftover which aborts install.
                self.log("Guide assumptions: clean PVE 8.3, PVE 8.3 needs 16.x/17.x drivers, revert any gpu-passthrough first.")
                self.log("Repo setup (pve-no-subscription vs enterprise) is manual per guide and NOT automated here.")
                self.run_remote_command("uname -a; cat /proc/cmdline; pveversion 2>/dev/null || echo '(pveversion not found)'", timeout=30)
                self.run_remote_command("df -h / /root /tmp | head -n 10", timeout=30)
                # Passthrough leftover check: if NVIDIA GPU already bound to vfio-pci, host-driver install will fail.
                # We only warn here; the hard abort happens before driver install (after user saw full log).
                self.run_remote_command("lspci -knnd 10de: 2>/dev/null || lspci -nn | grep -i nvidia || echo '(lspci found no 10de device)'", timeout=30)
                if cfg["gpu_type"] in ("consumer", "pascal"):
                    self.log("NOTE: RTX 30xx/40xx (Ampere/Ada) WILL NOT WORK unless vGPU-qualified (A5000/RTX 6000 Ada etc).")
                    self.log("Supported consumer: Maxwell 2.0 (except GTX 970), Pascal, Turing. Tested on RTX 2080 Ti (Turing).")
                if cfg["gpu_type"] == "pascal":
                    if version and version.startswith("550."):
                        self.log("NOTE (pascal + 17.x/550.*): guide recommends staying on 16.x LTS (16.9/535.230.02). 17.x needs patch + 16.x vgpuConfig.xml copy (see install step).")
                    else:
                        self.log("NOTE (pascal): guide recommends latest 16.x (16.9/535.230.02). Patch is still required.")
                if steps["install_driver"] or steps["patch_driver"]:
                    self.log(f"Driver file must be NVIDIA-Linux-x86_64-{version}-vgpu-kvm.run from Host_Drivers (Licensing Portal), uploaded to /root/.")

                # Step 1: Install prerequisite packages (per readme + noninteractive for automation)
                if steps["install_prereq"]:
                    self.log("\n=== Step: Install prerequisite packages ===")
                    # Heal interrupted dpkg first so apt never deadlocks on lock/journal.
                    self.run_remote_command("dpkg --configure -a || true", timeout=300)
                    self.run_remote_command("export DEBIAN_FRONTEND=noninteractive; apt-get update", timeout=300)
                    if not self.run_remote_command(
                        "export DEBIAN_FRONTEND=noninteractive; "
                        "apt-get dist-upgrade -y -o Dpkg::Options::=\"--force-confdef\" "
                        "-o Dpkg::Options::=\"--force-confold\"",
                        timeout=1800,
                    ):
                        self.log("dist-upgrade reported failure, continuing (check log above)...")
                    if not self.run_remote_command(
                        "export DEBIAN_FRONTEND=noninteractive; "
                        "apt-get install -y git build-essential dkms pve-headers mdevctl curl procps pciutils",
                        timeout=900,
                    ):
                        self.log("Prerequisite install FAILED - later steps will likely fail. Aborting.")
                        return
                    # Sanity: headers present for running kernel, else DKMS build will fail later.
                    self.run_remote_command("uname -r; ls -d /lib/modules/$(uname -r)/build 2>/dev/null || echo 'WARNING: kernel headers missing for running kernel!'", timeout=30)

                # Step 2: Clone and build vgpu_unlock-rs (idempotent, upgrade-safe per readme)
                if steps["clone_build_unlock"]:
                    self.log("\n=== Step: Clone and build vgpu_unlock-rs ===")
                    # Readme upgrade path: delete folder OR git pull + rebuild. We pull, and on
                    # any local-change conflict we reset to upstream so reruns never wedge.
                    self.run_remote_command(
                        "if [ ! -d /opt/vgpu_unlock-rs ]; then "
                        "git clone https://github.com/mbilker/vgpu_unlock-rs.git /opt/vgpu_unlock-rs; "
                        "else echo 'vgpu_unlock-rs already cloned, updating...'; "
                        "git -C /opt/vgpu_unlock-rs pull --ff-only || "
                        "(echo 'pull failed, resetting to upstream...'; "
                        "git -C /opt/vgpu_unlock-rs fetch origin; "
                        "git -C /opt/vgpu_unlock-rs reset --hard origin/HEAD || true); fi",
                        timeout=180,
                    )
                    self.run_remote_command(
                        "if [ ! -x \"$HOME/.cargo/bin/cargo\" ] && [ ! -x /root/.cargo/bin/cargo ]; then "
                        "curl https://sh.rustup.rs -sSf | sh -s -- -y --profile minimal; "
                        "else echo 'cargo already installed'; fi",
                        timeout=600,
                    )
                    if not self.run_remote_command(
                        "export PATH=\"$HOME/.cargo/bin:/root/.cargo/bin:$PATH\"; "
                        "cd /opt/vgpu_unlock-rs && cargo build --release",
                        timeout=1800,
                    ):
                        self.log("cargo build FAILED. Aborting.")
                        return

                # Step 3: Configure vGPU unlock (avoid stale-state cross-contamination)
                if steps["configure_unlock"]:
                    self.log("\n=== Step: Configure vGPU unlock ===")
                    if cfg["gpu_type"] == "supported":
                        # Qualified GPU: unlock must stay OFF or it adds failure points (per readme).
                        self.run_remote_command("mkdir -p /etc/vgpu_unlock && printf 'unlock = false\\n' > /etc/vgpu_unlock/config.toml")
                    else:
                        # Consumer/pascal: unlock must be ON. A stale config.toml with
                        # unlock=false from a previous 'supported' run would silently
                        # disable unlock and yield empty 'mdevctl types'. Remove it.
                        self.run_remote_command("mkdir -p /etc/vgpu_unlock && touch /etc/vgpu_unlock/profile_override.toml")
                        self.run_remote_command("rm -f /etc/vgpu_unlock/config.toml; echo 'ensured no stale unlock=false (consumer/pascal needs unlock ON)'")
                    self.run_remote_command("mkdir -p /etc/systemd/system/nvidia-vgpud.service.d /etc/systemd/system/nvidia-vgpu-mgr.service.d")
                    self.run_remote_command(
                        "printf '[Service]\\nEnvironment=LD_PRELOAD=/opt/vgpu_unlock-rs/target/release/libvgpu_unlock_rs.so\\n' "
                        "> /etc/systemd/system/nvidia-vgpud.service.d/vgpu_unlock.conf"
                    )
                    self.run_remote_command(
                        "printf '[Service]\\nEnvironment=LD_PRELOAD=/opt/vgpu_unlock-rs/target/release/libvgpu_unlock_rs.so\\n' "
                        "> /etc/systemd/system/nvidia-vgpu-mgr.service.d/vgpu_unlock.conf"
                    )
                    self.run_remote_command("systemctl daemon-reload || true")
                    self.run_remote_command("test -f /opt/vgpu_unlock-rs/target/release/libvgpu_unlock_rs.so && echo 'unlock lib present' || echo 'WARNING: unlock lib missing!'")

                # Step 4: Enable IOMMU (per readme: Intel adds intel_iommu=on,
                # AMD adds nothing - amd_iommu=on does not exist. iommu=pt is
                # optional for perf issues only, so we do NOT add it automatically.)
                if steps["enable_iommu"]:
                    self.log("\n=== Step: Enable IOMMU ===")
                    self.log("NOTE: per guide IOMMU is optional for vGPU, BIOS Vt-d/AMD-Vi must be on first.")
                    use_systemd_boot = self.remote_file_exists("/etc/kernel/cmdline")
                    if cfg["cpu_vendor"] == "intel":
                        params = ["intel_iommu=on"]
                    else:
                        params = []
                        self.log("AMD selected: per guide no kernel param is added (amd_iommu=on does not exist).")
                        self.log("If you have heavy perf issues you may manually add 'iommu=pt' (loss of security/stability).")
                    if use_systemd_boot:
                        self.log("Bootloader: systemd-boot (/etc/kernel/cmdline)")
                        self.run_remote_command("test -f /etc/kernel/cmdline.bak || cp /etc/kernel/cmdline /etc/kernel/cmdline.bak")
                        for p in params:
                            self.run_remote_command(
                                f"grep -qw {shlex.quote(p)} /etc/kernel/cmdline 2>/dev/null "
                                f"|| sed -i 's/$/ {p}/' /etc/kernel/cmdline"
                            )
                        self.run_remote_command("cat /etc/kernel/cmdline")
                        if not self.run_remote_command("proxmox-boot-tool refresh", timeout=180):
                            self.log("WARNING: proxmox-boot-tool refresh failed (check log). IOMMU change may not persist.")
                    else:
                        self.log("Bootloader: GRUB (/etc/default/grub)")
                        self.run_remote_command("test -f /etc/default/grub.bak || cp /etc/default/grub /etc/default/grub.bak")
                        for p in params:
                            # Append inside the GRUB_CMDLINE_LINUX_DEFAULT quotes if not already present.
                            # Fallback: if the variable line is missing entirely, append a new one.
                            self.run_remote_command(
                                f"grep -qw {shlex.quote(p)} /etc/default/grub 2>/dev/null || "
                                f"(grep -q '^GRUB_CMDLINE_LINUX_DEFAULT=' /etc/default/grub && "
                                f"sed -i 's/^GRUB_CMDLINE_LINUX_DEFAULT=\"\\([^\"]*\\)\"/GRUB_CMDLINE_LINUX_DEFAULT=\"\\1 {p}\"/' /etc/default/grub || "
                                f"echo 'GRUB_CMDLINE_LINUX_DEFAULT=\"quiet {p}\"' >> /etc/default/grub)"
                            )
                        if not params:
                            self.log("AMD + GRUB: no param to add, leaving GRUB_CMDLINE_LINUX_DEFAULT unchanged (per guide).")
                        self.run_remote_command("grep '^GRUB_CMDLINE_LINUX_DEFAULT' /etc/default/grub || echo '(GRUB_CMDLINE_LINUX_DEFAULT not found!)'")
                        if not self.run_remote_command("update-grub", timeout=180):
                            self.log("WARNING: update-grub failed (check log). IOMMU change may not persist.")

                # Step 5: Load kernel modules and blacklist nouveau (idempotent, per readme)
                if steps["load_modules"]:
                    self.log("\n=== Step: Load kernel modules and blacklist nouveau ===")
                    for mod in ("vfio", "vfio_iommu_type1", "vfio_pci", "vfio_virqfd"):
                        self.run_remote_command(f"grep -qxF {shlex.quote(mod)} /etc/modules 2>/dev/null || echo {shlex.quote(mod)} >> /etc/modules")
                    self.run_remote_command("grep -qxF 'blacklist nouveau' /etc/modprobe.d/blacklist.conf 2>/dev/null || echo 'blacklist nouveau' >> /etc/modprobe.d/blacklist.conf")
                    self.run_remote_command("cat /etc/modules; echo '---'; cat /etc/modprobe.d/blacklist.conf")
                    if not self.run_remote_command("update-initramfs -u -k all", timeout=900):
                        self.log("update-initramfs FAILED. Aborting before reboot to avoid unbootable initramfs.")
                        return

                # Step 6: Upload driver and patch
                if steps["patch_driver"] or steps["install_driver"]:
                    self.log("\n=== Step: Upload driver ===")
                    local_driver = cfg["driver"]
                    remote_driver = f"/root/{os.path.basename(local_driver)}"
                    if not self.upload_file(local_driver, remote_driver):
                        self.log("Failed to upload driver, aborting.")
                        return
                    self.run_remote_command(f"chmod +x {shlex.quote(remote_driver)}")
                    version = self.get_driver_version(os.path.basename(local_driver))
                    if not version:
                        self.log("Could not determine driver version from filename. Aborting patch/install.")
                        return
                    self.log(f"Detected driver version: {version}")

                # Step 7: Patch driver
                install_driver_file = remote_driver  # default; overwritten below if patch succeeds
                if steps["patch_driver"]:
                    self.log("\n=== Step: Patch driver ===")
                    if cfg["gpu_type"] == "supported":
                        self.log("GPU is vGPU supported; patch not required.")
                        install_driver_file = remote_driver
                    else:
                        patch_dir = cfg["patch_dir"]
                        patch_path = os.path.join(patch_dir, f"{version}.patch")
                        if not os.path.isfile(patch_path):
                            if cfg["auto_download"]:
                                patch_path = self.download_patch(version, patch_dir)
                            else:
                                self.log(f"Patch file not found: {patch_path}. Auto-download disabled.")
                                patch_path = None
                        if patch_path and os.path.isfile(patch_path):
                            remote_patch = f"/root/{os.path.basename(patch_path)}"
                            if not self.upload_file(patch_path, remote_patch):
                                self.log("Patch upload failed. Aborting (refusing to install unpatched driver silently).")
                                return
                            # Pin CWD to /root so the -custom.run lands where we verify it.
                            # Readme: ./NVIDIA-...run --apply-patch ~/vgpu-proxmox/550.144.02.patch
                            patched_cmd = f"cd /root && {shlex.quote(remote_driver)} --apply-patch {shlex.quote(remote_patch)}"
                            if not self.run_remote_command(patched_cmd, timeout=600):
                                self.log("Patch step FAILED. Aborting (refusing to install unpatched driver silently).")
                                return
                            # Determine expected patched filename.
                            if remote_driver.endswith("-vgpu-kvm.run"):
                                custom_driver = remote_driver.replace("-vgpu-kvm.run", "-vgpu-kvm-custom.run")
                            else:
                                custom_driver = remote_driver.replace(".run", "-custom.run")
                            if not self.remote_file_exists(custom_driver):
                                self.log(f"Patched file not found at expected path: {custom_driver}")
                                # Try to discover it rather than guessing wrong.
                                self.run_remote_command("ls -lt /root/*custom*.run 2>/dev/null | head -n 5 || echo 'no custom driver found in /root'")
                                self.log("Aborting: patch may have failed or used a different output name.")
                                return
                            self.log(f"Patched driver ready: {custom_driver}")
                            install_driver_file = custom_driver
                        else:
                            self.log("No patch available. Aborting (refusing to install unpatched driver on consumer GPU).")
                            return

                # Step 8: Install driver (per readme: --dkms -m=kernel; --silent for automation)
                if steps["install_driver"]:
                    self.log("\n=== Step: Install driver ===")
                    if not install_driver_file:
                        self.log("Internal error: no driver file selected for install. Aborting.")
                        return
                    # Hard abort if GPU is still bound to vfio-pci from old passthrough (readme: must revert ALL steps).
                    self.run_remote_command("lspci -knnd 10de: 2>/dev/null | head -n 20", timeout=30)
                    # Upgrade path per readme: nvidia-uninstall old driver first if present.
                    self.run_remote_command("test -x /usr/bin/nvidia-uninstall && nvidia-uninstall --silent || echo 'no previous nvidia-uninstall, fresh install'", timeout=600)
                    # Readme uses: ./NVIDIA-...run --dkms -m=kernel (interactive, answers Yes to DKMS prompt).
                    # For GUI automation we add --silent (implies no-questions) but keep -m=kernel.
                    # Pin CWD to /root for consistent installer temp/output paths.
                    install_cmd = f"cd /root && sh {shlex.quote(install_driver_file)} --silent --dkms -m=kernel"
                    if not self.run_remote_command(install_cmd, timeout=1800):
                        self.log("Driver install FAILED. Not continuing to verify as success.")
                        self.log("Common causes per guide: leftover gpu-passthrough (vfio-pci bound, check lspci -knnd 10de:), wrong driver/PVE kernel combo.")
                        return
                    self.log("Driver installer reported success.")
                    # Pascal/older on 17.x needs extra vgpuConfig.xml step per readme PSA.
                    if cfg["gpu_type"] == "pascal" and version and version.startswith("550."):
                        self.log("WARNING (pascal + 17.x): per guide you must also replace vgpuConfig.xml with the 16.x copy:")
                        self.log("  1) extract 16.x driver on host: sh NVIDIA-Linux-x86_64-535.230.02-vgpu-kvm.run -x")
                        self.log("  2) cp <extracted>/vgpuConfig.xml /usr/share/nvidia/vgpu/vgpuConfig.xml (overwrite 17.x file)")
                        self.log("  3) reboot, then check 'mdevctl types'. Consider staying on 16.x LTS (16.9/535.230.02) for Pascal.")
                        self.run_remote_command("ls -l /usr/share/nvidia/vgpu/vgpuConfig.xml || echo 'vgpuConfig.xml not found yet'", timeout=30)

                # Step 9: Reboot (terminal for this run)
                if steps["reboot_after"]:
                    self.log("\n=== Step: Reboot ===")
                    self.run_remote_command("reboot || systemctl reboot || true", timeout=30)
                    self.log("Reboot command sent. Host is going down; VERIFY is skipped in this run.")
                    self.log("Wait 2-5 minutes, then re-run with only 'Verify installation' checked.")
                    return

                # Step 10: Verify (skipped if we just rebooted; per readme checks + IOMMU dmesg)
                if steps["verify"]:
                    self.log("\n=== Step: Verify ===")
                    self.run_remote_command("nvidia-smi || echo 'nvidia-smi FAILED'", timeout=60)
                    self.run_remote_command("mdevctl types || echo 'mdevctl types FAILED (empty = unlock not working; check unlock=false vs profile_override)'", timeout=60)
                    self.run_remote_command("nvidia-smi vgpu || echo 'nvidia-smi vgpu FAILED (may be normal without vGPU guests)'", timeout=60)
                    self.run_remote_command("lsmod | grep -E 'nvidia|vfio' || echo '(no nvidia/vfio in lsmod)'", timeout=30)
                    self.run_remote_command("dmesg | grep -e DMAR -e IOMMU | head -n 20 || echo '(no DMAR/IOMMU in dmesg; see guide IOMMU section - optional)'", timeout=30)
                    self.run_remote_command("ls -l /usr/share/nvidia/vgpu/vgpuConfig.xml 2>/dev/null || echo '(no vgpuConfig.xml yet - only needed for pascal+17.x)'", timeout=30)

                self.log("\n=== All selected steps completed ===")

            except Exception as e:
                self.log(f"Unexpected error: {e}\n{traceback.format_exc(limit=5)}")
            finally:
                self.disconnect_ssh()
                self.root.after(0, self._reenable_buttons)

        threading.Thread(target=_run, daemon=True).start()

    def _reenable_buttons(self):
        self._running = False
        try:
            self.run_btn.config(state='normal')
        except Exception:
            pass
        try:
            self.test_btn.config(state='normal')
        except Exception:
            pass

if __name__ == "__main__":
    root = tk.Tk()
    app = ProxmoxVGPUInstallerGUI(root)
    root.mainloop()
