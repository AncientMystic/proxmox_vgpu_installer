# Proxmox vGPU Installer GUI

A Python-based GUI tool that automates the installation of NVIDIA vGPU drivers on a Proxmox VE host over SSH.  
It provides a user-friendly interface to upload the driver, apply patches, configure vGPU unlock, enable IOMMU, and verify the installation – with each major step optional and a live log to guide you.

> **Important:** This tool is a wrapper around the excellent [Proxmox vGPU guide by polloloco](https://gitlab.com/polloloco/vgpu-proxmox).  
> **Please read the full guide** to understand the process, requirements, and potential pitfalls before using this tool.  
> The GUI automates the commands, but you should be familiar with what each step does.

---

**WARNING:** currently not completely tested. it should work but i do not have a spare machine to setup from zero currently. so please report issues. 

---

## Screenshot

![Proxmox vGPU Installer GUI](https://github.com/AncientMystic/proxmox_vgpu_installer/blob/main/proxmox-vgpu-installer.jpg)

---

## Features

- SSH connection to a remote Proxmox host (password authentication).
- Browse and upload the NVIDIA vGPU driver `.run` file.
- Automatic driver version detection from the filename.
- Optional automatic download of the correct patch from the official repository.
- Selectable steps (checkboxes) for granular control:
  - Install prerequisite packages
  - Clone and build `vgpu_unlock-rs`
  - Configure vGPU unlock (systemd overrides)
  - Enable IOMMU (Intel/AMD)
  - Load kernel modules and blacklist `nouveau`
  - Patch the driver (consumer GPUs only)
  - Install the driver
  - Reboot after installation
  - Verify installation (`nvidia-smi`, `mdevctl types`, `nvidia-smi vgpu`)
- GPU type selection (consumer, vGPU supported, Pascal/older).
- CPU vendor selection for correct IOMMU kernel parameters.
- Live log showing all remote command output and errors.
- Threaded execution – GUI stays responsive during long operations.
- Connection test button to validate SSH credentials.

---

## Requirements

- **Python 3.6+**
- **Paramiko** library (for SSH/SFTP)
- **Tkinter** (usually included with Python on Windows/macOS; on Linux install `python3-tk`)

Install the Python dependency:

```bash
pip install paramiko
```

---

## Usage

1. Clone or download this repository.
2. Install the required library (`paramiko`).
3. Run the script:

```bash
python proxmox_vgpu_installer.py
```

4. Fill in the connection details:
   - **Host**: IP or hostname of your Proxmox server.
   - **Username**: usually `root`.
   - **Password**: root password.
   - **Port**: SSH port (default 22).
5. Click **Test Connection** to verify SSH access.
6. Browse and select the NVIDIA vGPU driver `.run` file (e.g., `NVIDIA-Linux-x86_64-550.144.02-vgpu-kvm.run`).
7. Choose a local patch folder or enable **Auto-download patch**.
8. Select the GPU type and CPU vendor.
9. Check or uncheck the steps you want to execute.
10. Click **Run Selected Steps** and monitor the log.

> **Note:** The tool assumes you are logged in as `root`. If you use a different user, you must manually adjust the commands for `sudo` (not currently implemented as it is not supported by proxmox.).

---

## Credits & Thanks

### Special Thanks to polloloco

This tool is entirely based on the detailed and invaluable guide by **polloloco**:  
[Proxmox vGPU Guide – GitLab](https://gitlab.com/polloloco/vgpu-proxmox)

---

### Further Thanks

The original guide acknowledges the contributions of many individuals. We extend our gratitude to all of them:

- **DualCoder** – for the original `vgpu_unlock` repository with kernel hooks.
- **mbilker** – for the Rust version, `vgpu_unlock-rs`.
- **KrutavShah** – for the GPU Virtualization Wiki.
- **HiFiPhile** – for the C version of vgpu unlock.
- **rupansh** – for the original `twelve.patch` for kernels ≥ 5.12.
- **mbuchel#1878** – for `fourteen.patch` for kernels ≥ 5.14.
- **erin-allison** – for the `nvidia-smi` wrapper script.
- **LIL'pingu#9069** – for the patch to disable NVIDIA's anti-consumer checks.
- **GreenDam** – for Linux kernel 6.8 support for drivers 16.5 and 17.1.

Their collective work made this tool possible.

---

## Disclaimer

This tool is provided as-is, without warranty of any kind. Use it at your own risk.  
Always back up your system and read the original guide thoroughly before proceeding.  
The author(s) of this GUI are not responsible for any damage caused by misuse or unexpected behaviour.
