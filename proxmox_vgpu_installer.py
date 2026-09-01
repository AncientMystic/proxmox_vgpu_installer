import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
import paramiko
import os
import re
import threading
import queue
import time
from pathlib import Path

class ProxmoxVGPUInstallerGUI:
    PATCH_BASE_URL = "https://gitlab.com/polloloco/vgpu-proxmox/-/raw/master/"

    def __init__(self, root):
        self.root = root
        self.root.title("Proxmox NVIDIA vGPU Installer")
        self.root.geometry("900x700")

        # SSH client
        self.ssh = None
        self.sftp = None

        # Queue for thread-safe logging
        self.log_queue = queue.Queue()

        # Variables
        self.host_var = tk.StringVar()
        self.user_var = tk.StringVar(value="root")
        self.password_var = tk.StringVar()
        self.port_var = tk.IntVar(value=22)

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

    def build_gui(self):
        # Main container
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # Connection frame
        conn_frame = ttk.LabelFrame(main_frame, text="SSH Connection", padding="5")
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
        ttk.Button(conn_frame, text="Test Connection", command=self.test_connection).grid(row=2, column=0, columnspan=4, pady=5)

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

    def browse_driver(self):
        filename = filedialog.askopenfilename(title="Select NVIDIA driver .run file", filetypes=[("NVIDIA driver", "*.run"), ("All files", "*.*")])
        if filename:
            self.driver_path_var.set(filename)

    def browse_patch_dir(self):
        dirname = filedialog.askdirectory(title="Select patch folder")
        if dirname:
            self.patch_dir_var.set(dirname)

    def log(self, message):
        """Add a message to the log queue."""
        self.log_queue.put(message)

    def process_log_queue(self):
        """Process pending log messages and update the text widget."""
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

    def connect_ssh(self):
        """Establish SSH connection."""
        try:
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh.connect(
                hostname=self.host_var.get(),
                port=self.port_var.get(),
                username=self.user_var.get(),
                password=self.password_var.get(),
                timeout=10
            )
            self.sftp = self.ssh.open_sftp()
            return True
        except Exception as e:
            self.log(f"SSH connection failed: {str(e)}")
            return False

    def test_connection(self):
        """Test SSH connection in a separate thread."""
        def _test():
            if self.connect_ssh():
                self.log("SSH connection successful.")
                self.ssh.close()
                self.sftp.close()
            else:
                self.log("SSH connection test failed.")
        threading.Thread(target=_test, daemon=True).start()

    def run_remote_command(self, command, sudo=False, timeout=None):
        """Execute a command on the remote host and log output."""
        if not self.ssh:
            self.log("ERROR: SSH not connected.")
            return False

        if sudo and self.user_var.get() != "root":
            command = f"echo '{self.password_var.get()}' | sudo -S {command}"

        self.log(f"$ {command}")
        try:
            stdin, stdout, stderr = self.ssh.exec_command(command, timeout=timeout, get_pty=True)
            # Read output as it comes
            for line in iter(stdout.readline, ""):
                self.log(line.strip())
            for line in iter(stderr.readline, ""):
                self.log(f"STDERR: {line.strip()}")
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                self.log(f"Command failed with exit code {exit_status}")
                return False
            return True
        except Exception as e:
            self.log(f"Command execution error: {str(e)}")
            return False

    def upload_file(self, local_path, remote_path):
        """Upload a file via SFTP."""
        try:
            self.sftp.put(local_path, remote_path)
            self.log(f"Uploaded {local_path} -> {remote_path}")
            return True
        except Exception as e:
            self.log(f"Upload failed: {str(e)}")
            return False

    def download_patch(self, version, local_dir):
        """Download patch from GitLab."""
        patch_filename = f"{version}.patch"
        url = self.PATCH_BASE_URL + patch_filename
        local_path = os.path.join(local_dir, patch_filename)

        if os.path.exists(local_path):
            self.log(f"Patch already exists locally: {local_path}")
            return local_path

        self.log(f"Downloading patch from {url}")
        import urllib.request
        try:
            urllib.request.urlretrieve(url, local_path)
            self.log(f"Downloaded patch to {local_path}")
            return local_path
        except Exception as e:
            self.log(f"Failed to download patch: {str(e)}")
            return None

    def get_driver_version(self, driver_filename):
        """Extract version from filename like NVIDIA-Linux-x86_64-550.144.02-vgpu-kvm.run"""
        match = re.search(r'NVIDIA-Linux-x86_64-(\d+\.\d+\.\d+)-vgpu-kvm\.run', driver_filename)
        if match:
            return match.group(1)
        return None

    def run_steps(self):
        """Execute selected steps in a separate thread."""
        if self.run_btn['state'] == 'disabled':
            return

        # Validate inputs
        if not self.host_var.get() or not self.password_var.get():
            messagebox.showerror("Error", "Host and password are required.")
            return

        if self.step_vars["patch_driver"].get() or self.step_vars["install_driver"].get():
            if not self.driver_path_var.get():
                messagebox.showerror("Error", "Driver file is required for patch/install steps.")
                return

        # Disable run button
        self.run_btn.config(state='disabled')
        self.clear_log()
        self.log("Starting installation process...")

        def _run():
            try:
                if not self.connect_ssh():
                    self.log("Aborting due to SSH connection failure.")
                    return

                # Step 1: Install prerequisite packages
                if self.step_vars["install_prereq"].get():
                    self.log("\n=== Step: Install prerequisite packages ===")
                    if not self.run_remote_command("apt update"):
                        self.log("apt update failed, continuing...")
                    if not self.run_remote_command("apt dist-upgrade -y", timeout=600):
                        self.log("dist-upgrade failed, continuing...")
                    self.run_remote_command("apt install -y git build-essential dkms pve-headers mdevctl", timeout=300)

                # Step 2: Clone and build vgpu_unlock-rs
                if self.step_vars["clone_build_unlock"].get():
                    self.log("\n=== Step: Clone and build vgpu_unlock-rs ===")
                    self.run_remote_command("cd /opt && git clone https://github.com/mbilker/vgpu_unlock-rs.git")
                    self.run_remote_command("curl https://sh.rustup.rs -sSf | sh -s -- -y --profile minimal", timeout=300)
                    self.run_remote_command("source $HOME/.cargo/env && cd /opt/vgpu_unlock-rs && cargo build --release", timeout=600)

                # Step 3: Configure vGPU unlock
                if self.step_vars["configure_unlock"].get():
                    self.log("\n=== Step: Configure vGPU unlock ===")
                    if self.gpu_type_var.get() == "supported":
                        self.run_remote_command("mkdir -p /etc/vgpu_unlock && echo 'unlock = false' > /etc/vgpu_unlock/config.toml")
                    else:
                        self.run_remote_command("mkdir -p /etc/vgpu_unlock && touch /etc/vgpu_unlock/profile_override.toml")
                    self.run_remote_command("mkdir -p /etc/systemd/system/{nvidia-vgpud.service.d,nvidia-vgpu-mgr.service.d}")
                    self.run_remote_command("echo -e '[Service]\\nEnvironment=LD_PRELOAD=/opt/vgpu_unlock-rs/target/release/libvgpu_unlock_rs.so' > /etc/systemd/system/nvidia-vgpud.service.d/vgpu_unlock.conf")
                    self.run_remote_command("echo -e '[Service]\\nEnvironment=LD_PRELOAD=/opt/vgpu_unlock-rs/target/release/libvgpu_unlock_rs.so' > /etc/systemd/system/nvidia-vgpu-mgr.service.d/vgpu_unlock.conf")

                # Step 4: Enable IOMMU
                if self.step_vars["enable_iommu"].get():
                    self.log("\n=== Step: Enable IOMMU ===")
                    # Determine bootloader (simplified: check for /etc/kernel/cmdline)
                    check = self.run_remote_command("test -f /etc/kernel/cmdline")
                    if check:
                        # systemd-boot
                        self.run_remote_command("cp /etc/kernel/cmdline /etc/kernel/cmdline.bak")
                        if self.cpu_vendor_var.get() == "intel":
                            self.run_remote_command("sed -i 's/$/ intel_iommu=on/' /etc/kernel/cmdline")
                        # AMD no extra option
                        self.run_remote_command("proxmox-boot-tool refresh")
                    else:
                        # GRUB
                        self.run_remote_command("cp /etc/default/grub /etc/default/grub.bak")
                        if self.cpu_vendor_var.get() == "intel":
                            self.run_remote_command("sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT=\"\\(.*\\)\"/GRUB_CMDLINE_LINUX_DEFAULT=\"\\1 intel_iommu=on\"/' /etc/default/grub")
                        self.run_remote_command("update-grub")

                # Step 5: Load kernel modules and blacklist nouveau
                if self.step_vars["load_modules"].get():
                    self.log("\n=== Step: Load kernel modules and blacklist nouveau ===")
                    self.run_remote_command("echo -e 'vfio\\nvfio_iommu_type1\\nvfio_pci\\nvfio_virqfd' >> /etc/modules")
                    self.run_remote_command("echo 'blacklist nouveau' >> /etc/modprobe.d/blacklist.conf")
                    self.run_remote_command("update-initramfs -u -k all", timeout=300)

                # Step 6: Upload driver and patch
                if self.step_vars["patch_driver"].get() or self.step_vars["install_driver"].get():
                    self.log("\n=== Step: Upload driver ===")
                    local_driver = self.driver_path_var.get()
                    remote_driver = f"/root/{os.path.basename(local_driver)}"
                    if not self.upload_file(local_driver, remote_driver):
                        self.log("Failed to upload driver, aborting.")
                        return
                    self.run_remote_command(f"chmod +x {remote_driver}")

                    # Get version
                    version = self.get_driver_version(os.path.basename(local_driver))
                    if not version:
                        self.log("Could not determine driver version from filename. Aborting patch/install.")
                        return
                    self.log(f"Detected driver version: {version}")

                # Step 7: Patch driver
                if self.step_vars["patch_driver"].get():
                    self.log("\n=== Step: Patch driver ===")
                    if self.gpu_type_var.get() != "supported":
                        patch_dir = self.patch_dir_var.get()
                        patch_path = os.path.join(patch_dir, f"{version}.patch")
                        if not os.path.exists(patch_path):
                            if self.auto_download_patch_var.get():
                                patch_path = self.download_patch(version, patch_dir)
                            else:
                                self.log(f"Patch file not found: {patch_path}. Skipping patch.")
                                patch_path = None
                        if patch_path:
                            remote_patch = f"/root/{os.path.basename(patch_path)}"
                            self.upload_file(patch_path, remote_patch)
                            # Apply patch
                            patched_cmd = f"{remote_driver} --apply-patch {remote_patch}"
                            self.run_remote_command(patched_cmd, timeout=300)
                            # Determine patched filename
                            custom_driver = remote_driver.replace("-vgpu-kvm.run", "-vgpu-kvm-custom.run")
                            if not os.path.basename(custom_driver).startswith("NVIDIA-Linux"):
                                custom_driver = remote_driver.replace(".run", "-custom.run")
                            # Set variable for install step
                            install_driver_file = custom_driver
                        else:
                            self.log("No patch available, cannot patch. Installing original driver?")
                            install_driver_file = remote_driver
                    else:
                        self.log("GPU is vGPU supported; patch not required.")
                        install_driver_file = remote_driver
                else:
                    install_driver_file = remote_driver

                # Step 8: Install driver
                if self.step_vars["install_driver"].get():
                    self.log("\n=== Step: Install driver ===")
                    install_cmd = f"{install_driver_file} --silent --dkms -m=kernel"
                    self.run_remote_command(install_cmd, timeout=600)

                # Step 9: Reboot
                if self.step_vars["reboot_after"].get():
                    self.log("\n=== Step: Reboot ===")
                    self.run_remote_command("reboot")
                    # Wait for reboot and reconnect?
                    self.log("System is rebooting. Please wait and manually reconnect later.")
                    time.sleep(5)
                    # We could attempt to reconnect after some time, but skip for now.

                # Step 10: Verify
                if self.step_vars["verify"].get():
                    self.log("\n=== Step: Verify ===")
                    # nvidia-smi
                    self.run_remote_command("nvidia-smi")
                    # mdevctl types
                    self.run_remote_command("mdevctl types")
                    # nvidia-smi vgpu
                    self.run_remote_command("nvidia-smi vgpu")
                    # Additional checks can be added here

                self.log("\n=== All selected steps completed ===")

            except Exception as e:
                self.log(f"Unexpected error: {str(e)}")
            finally:
                if self.ssh:
                    self.ssh.close()
                if self.sftp:
                    self.sftp.close()
                self.root.after(0, lambda: self.run_btn.config(state='normal'))

        threading.Thread(target=_run, daemon=True).start()

if __name__ == "__main__":
    root = tk.Tk()
    app = ProxmoxVGPUInstallerGUI(root)
    root.mainloop()