# BizonHelp – Bash Toolkit for Bizon Workstations

A curated `.bashrc` configuration that provides command-line tools, aliases, and helper functions for managing Bizon AI workstations running BizonOS (Ubuntu-based).

---

## GPU QA release candidate

`gpu-qa` adds AI inference/training, VRAM and bandwidth checks, and 1–8 GPU orchestration with HTML/PDF evidence reports. The candidate is software-tested; physical GPU qualification is pending and production approval is blocked by default.

See [installation and team commands](gpu-qa/README.md) and the [hardware qualification checklist](gpu-qa/VALIDATION.md). The GPU QA installer preserves existing shell settings.

## Installation

Copy the `bashrc` file to your home directory:

```bash
cp bashrc ~/.bashrc
source ~/.bashrc
```

---

## What's New in v2

### Changed
- **`bizonapp-stop`** – Now only stops the Bizon API server (camera services removed)
- **`bizonapp-start`** – Now only starts the Bizon API server (camera services removed)
- **`bizonapp-status`** – Now only shows Bizon API server status (camera services removed)
- **Help text** updated to reflect API-only service management

### Removed
- All references to `bizon-camera` and `bizon-static-camera` services
- Camera service management from start/stop/status functions

---

## Available Commands

Type `bizonhelp` in the terminal to see the full list. Here's a summary:

### System & Hardware
| Command | Description |
|---------|-------------|
| `gpu-info` | Show GPU index, PCI bus, serial, and UUID |
| `gpu-test` | Run the existing GPU Burn stress test |
| `gpu-qa` | Candidate AI workload QA, multi-GPU checks and reports; see its installation guide |
| `cpu-test` | Launch s-tui CPU stress test |
| `nvme-test` | Run fio NVMe stress test |
| `nvme-stress` | NVMe stress test suite |
| `Nvlink` | Show NVLink status |
| `Nvidia-full` | Full nvidia-smi query for GPU 0 |

### IPMI
| Command | Description |
|---------|-------------|
| `ipmipassreset` | Auto-detect and reset IPMI password |
| `ipmi-clear-cache` | Flush IPMI sensor cache |
| `ipmi-monitor` | Live IPMI sensor monitoring |
| `ipmiview` | Launch IPMIView Java app |

### Bizon API Server
| Command | Description |
|---------|-------------|
| `bizonapp-start` | Start the Bizon API server |
| `bizonapp-stop` | Stop the Bizon API server |
| `bizonapp-status` | Show Bizon API server status |
| `bizonapp-update` | Update the Bizon API server from GitHub |

### RAID
| Command | Description |
|---------|-------------|
| `raid-manager` | Open Adaptec RAID manager in browser |

### AI & Dev Tools
| Command | Description |
|---------|-------------|
| `lmstudio-install` | Download and install LM Studio |
| `lmstudio-launch` | Launch LM Studio |
| `cursor-launch` | Launch Cursor AI editor |

### Jupyter
| Command | Description |
|---------|-------------|
| `jupyter-local` | Start Jupyter Notebook locally |
| `jupyter-remote` | Start Jupyter for remote access (port 8889) |
| `jupyter-help` | Show Jupyter remote access instructions |

### Virtualization
| Command | Description |
|---------|-------------|
| `vm-win11` | Install Windows 11 VM via automated script |

### Remote Access
| Command | Description |
|---------|-------------|
| `anydesk-restore` | Fix AnyDesk if it stops working |

---

## Bizon API Server Management

The `bizonapp-*` commands manage the Bizon API server service:

```bash
# Stop the API server
bizonapp-stop

# Start the API server
bizonapp-start

# Check if the API server is running
bizonapp-status

# Update the API server from GitHub
bizonapp-update
```

---

## License

Proprietary – Bizon Tech Inc. Internal use only.
