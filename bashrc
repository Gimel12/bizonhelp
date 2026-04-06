# ~/.bashrc: executed by bash(1) for non-login shells.

export PATH=/usr/local/cuda/bin${PATH:+:${PATH}}
export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH

# If not running interactively, don't do anything
case $- in
    *i*) ;;
      *) return;;
esac

# Check terminal window size
shopt -s checkwinsize


# Force color prompt
force_color_prompt=yes

if [ -n "$force_color_prompt" ]; then
    if [ -x /usr/bin/tput ] && tput setaf 1 >&/dev/null; then
        color_prompt=yes
    else
        color_prompt=
    fi
fi

if [ "$color_prompt" = yes ]; then
    PS1='${debian_chroot:+($debian_chroot)}\[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ '
else
    PS1='${debian_chroot:+($debian_chroot)}\u@\h:\w\$ '
fi



# Enable color support
if [ -x /usr/bin/dircolors ]; then
    eval "$(dircolors -b)"
    alias ls='ls --color=auto'
    alias grep='grep --color=auto'
    alias fgrep='fgrep --color=auto'
    alias egrep='egrep --color=auto'
fi

# Aliases for quick file listing
alias ll='ls -alF'
alias la='ls -A'
alias l='ls -CF'

# BIZON HELP MENU
alias bizonhelp='
echo Bizonhelp_4.1
echo -e "\033[0;34m---------------Troubleshooting-commands--------------\033[0m"
echo -e cpu-test ----------"\033[31;1mStress test for your CPU\033[0m"
echo -e gpu-test ----------"\033[31;1mWill run luxmark benchmark to test your GPUs\033[0m"
echo -e nvme-test ---------"\033[31;1mCheck NVMe performance and health\033[0m"
echo -e Nvlink ------------"\033[31;1mCheck the status of NVLinks\033[0m"
echo -e Nvidia-full -------"\033[31;1mFull GPU info list\033[0m"
echo -e jupyter-local -----"\033[31;1mStart a local Jupyter notebook\033[0m"
echo -e jupyter-remote ----"\033[31;1mStart a remote Jupyter notebook\033[0m"
echo -e jupyter-help ------"\033[31;1mInstructions for accessing a remote Jupyter notebook\033[0m"
echo
echo -e "\033[0;34m---------------IPMI Control---------------\033[0m"
echo -e ipmipassreset -----"\033[31;1mReset IPMI admin password to 'Bizon123'\033[0m"
echo -e ipmi-clear-cache --"\033[31;1mClear IPMI sensor cache\033[0m"
echo -e ipmi-monitor ------"\033[31;1mLive monitoring of IPMI sensors\033[0m"
echo
echo -e "\033[0;34m---------------VM & Tools---------------\033[0m"
echo -e anydesk-restore ---"\033[31;1mRestore new Anydesk ID\033[0m"
echo -e ipmiview ----------"\033[31;1mLaunch SuperMicro IPMIView\033[0m"
echo -e raid-manager ------"\033[31;1mBizon Raid manager\033[0m"
echo -e nvme-stress -------"\033[31;1mBizon NVMe tool to stress the drives\033[0m"
echo
echo -e "\033[31;1m-----------------Beta Features----------------\033[0m"
echo -e ollama-install ----"\033[31;1mInstall Ollama for LLM interaction\033[0m"
echo -e lmstudio-install --"\033[31;1mInstall LM Studio (latest version)\033[0m"
echo -e lmstudio-launch ---"\033[31;1mLaunch LM Studio\033[0m"
echo -e cursor-launch -----"\033[31;1mLaunch Cursor AI coding assistant\033[0m"
echo
echo -e "\033[31;1m---------Bizon API & AI Services---------\033[0m"
echo -e claude-api --------"\033[31;1mSet/replace/remove Anthropic API key (usage: claude-api sk-ant-xxx | claude-api remove)\033[0m"
echo -e vllm-start --------"\033[31;1mStart vLLM inference server (Gemma 4 31B)\033[0m"
echo -e vllm-stop ---------"\033[31;1mStop vLLM inference server\033[0m"
echo -e vllm-status -------"\033[31;1mCheck vLLM server status\033[0m"
echo -e bizonapp-stop -----"\033[31;1mStop Bizon API server\033[0m"
echo -e bizonapp-start ----"\033[31;1mStart Bizon API server\033[0m"
echo -e bizonapp-status ---"\033[31;1mShow Bizon API server status\033[0m"
echo -e bizonapp-update ---"\033[31;1mUpdate Bizon API server\033[0m"
echo -e bizon-mcp-help ----"\033[31;1mHow to connect the Bizon MCP server to Claude Code or other AIs\033[0m"
echo'

# Nvme stress tool 
alias nvme-stress='
echo -e bizonapp-update ---"\033[31;1mUpdate Bizon API server\033[0m"
cd /usr/local/share/dlbt_os/bza/nvme_stress_test
python3 app.py
'


# Raid manager launcher 
alias raid-manager='
cd /usr/local/share/dlbt_os/bza/raid/raid_gui_bizonOS
python3 main.py
'

# GPU identification 
# GPU identification
alias gpu-info='nvidia-smi --query-gpu=index,gpu_bus_id,serial,uuid --format=csv | awk -F ", " '\''NR==1 {print "GPU,pci.bus_id,serial,uuid"} NR>1 {print "GPU " $1 "," $2 "," $3 ",\033[31m" $4 "\033[0m"}'\'''

# IPMI Password Reset Function
function auto_set_ipmi_password() {
    user_id=$(sudo ipmitool user list 1 | awk '/ADMIN|admin/ {print $1}')
    if [ -z "$user_id" ]; then
        echo "No admin account found. Try 'sudo ipmitool bmc reset cold' to reset the BMC."
    else
        sudo ipmitool user set password "$user_id" "Bizon123"
        echo "Password reset for user ID $user_id."
    fi
}
alias ipmipassreset='auto_set_ipmi_password'

# IPMI Monitoring Aliases
alias ipmi-clear-cache='sudo ipmi-sensors --flush-cache'
alias ipmi-monitor='sudo ipmi-sensors --flush-cache && sudo ipmi-sensors && watch -n.1 "sudo ipmi-sensors | grep -v N/A"'

# LM Studio Installation and Launch
alias lmstudio-install='
cd /home/bizon/Downloads
wget https://www.dropbox.com/s/6gy4sh28lwnndb5/LM-Studio-0.3.12-1-x64.AppImage
sudo chmod +x LM-Studio-0.3.12-1-x64.AppImage
echo "Run lmstudio-launch to start the app."
'
alias lmstudio-launch='cd /home/bizon/Downloads && ./LM-Studio-0.3.12-1-x64.AppImage --no-sandbox'

# Cursor AI Code Assistant
alias cursor-launch='
cd /home/bizon/Downloads
sudo ./Cursor-0.47.0-4a602340d7b014d700647120bae9079607f2ae9b.deb.glibc2.25-x86_64.AppImage --no-sandbox
'

# GPU & CPU Test Aliases
alias cpu-test='s-tui'
alias gpu-test='
cd /home/bizon/Downloads
sudo git clone https://github.com/wilicc/gpu-burn.git
cd gpu-burn/
sudo make 
./gpu_burn -tc 3600
'
alias nvme-test='
echo "NVMe test tools available in /home/bizon/.tools"
echo "Run: ./nvmecmd self-test.cmd.json -n 1 (use 1,2,3 for different NVMe drives)"
echo "Run: ./nvmecmd self-test.cmd.json --extended -n 1"
'
alias Nvlink='nvidia-smi nvlink --status'
alias Nvidia-full='nvidia-smi -i 0 -q'

# Jupyter Notebook Aliases
alias jupyter-local='jupyter notebook'
alias jupyter-remote='jupyter notebook --no-browser --port=8889'
alias jupyter-help='
echo "To access Jupyter remotely:"
echo "1. Start Jupyter with jupyter-remote"
echo "2. On client machine, run: ssh -N -f -L localhost:8888:localhost:8889 user@server"
echo "3. Open browser and go to http://localhost:8888/tree"
echo "4. Enter the token from the host"
'

# System Management Aliases
alias anydesk-restore='
sudo rm -f /etc/anydesk/service.conf
sudo reboot
'
alias vm-win11='/bin/bash -c "$(curl -fsSL https://bizon-vm.s3.amazonaws.com/win11_vm.sh)"'

# GPU Fan Control Functions
fans-gpu0() {
	nvidia-settings --display :1.0 -a "[gpu:0]/GPUFanControlState=1" -a "[fan]/GPUTargetFanSpeed=$1"
	echo "Fan speeds set to $1 percent"
}    

fans-gpu1() {
	nvidia-settings --display :1.0 -a "[gpu:1]/GPUFanControlState=1" -a "[fan]/GPUTargetFanSpeed=$1"
	echo "Fan speeds set to $1 percent"
}

# ─── Bizon API & AI Services ─────────────────────────────────────────────────

# Set Claude API key for the Bizon API diagnostic agent.
# This writes the key into the systemd service so it persists across reboots.
# Usage: claude-api sk-ant-api03-your-key-here
claude-api() {
    # Show current status if no args
    if [ -z "$1" ]; then
        echo -e "\033[31mUsage:\033[0m"
        echo -e "  claude-api <your-anthropic-api-key>   Set or replace the API key"
        echo -e "  claude-api remove                     Remove the API key"
        echo ""
        local current
        current=$(sudo grep -oP 'ANTHROPIC_API_KEY=\K.*' /etc/systemd/system/bizon-api.service 2>/dev/null)
        if [ -n "$current" ]; then
            local masked="${current:0:12}...${current: -4}"
            echo -e "Current key: \033[32m$masked\033[0m"
        else
            echo -e "Current key: \033[31mnot set\033[0m"
        fi
        return 1
    fi

    # Handle remove
    if [ "$1" = "remove" ] || [ "$1" = "clear" ] || [ "$1" = "unset" ]; then
        if ! sudo grep -q 'ANTHROPIC_API_KEY' /etc/systemd/system/bizon-api.service 2>/dev/null; then
            echo -e "\033[33mNo API key is currently set.\033[0m"
            return 0
        fi
        sudo sed -i '/Environment=ANTHROPIC_API_KEY=.*/d' /etc/systemd/system/bizon-api.service
        sudo systemctl daemon-reload
        sudo systemctl restart bizon-api.service
        echo -e "\033[32m✓ API key removed and Bizon API service restarted.\033[0m"
        echo -e "Claude backend is now disabled. Local backends (vllm, ollama) still work."
        return 0
    fi

    local key="$1"

    # Validate key format
    if [[ ! "$key" =~ ^sk-ant- ]]; then
        echo -e "\033[31mWarning: Key doesn't start with 'sk-ant-'. Are you sure this is correct?\033[0m"
    fi

    # Check if the Environment line already exists
    if sudo grep -q 'ANTHROPIC_API_KEY' /etc/systemd/system/bizon-api.service 2>/dev/null; then
        # Replace existing key
        sudo sed -i "s|Environment=ANTHROPIC_API_KEY=.*|Environment=ANTHROPIC_API_KEY=${key}|" /etc/systemd/system/bizon-api.service
    else
        # Add the key after the NODE_ENV line
        sudo sed -i "/Environment=NODE_ENV=production/a Environment=ANTHROPIC_API_KEY=${key}" /etc/systemd/system/bizon-api.service
    fi

    # Reload and restart
    sudo systemctl daemon-reload
    sudo systemctl restart bizon-api.service

    echo -e "\033[32m✓ API key set and Bizon API service restarted.\033[0m"
    echo -e "You can now use Claude as the AI backend from the mobile app."
    echo -e "Verify: curl -s http://localhost:4000/api/diagnostic/backends | python3 -m json.tool"
}

# vLLM server management
# Start the vLLM inference server with Gemma 4 31B (AWQ 4-bit) on the local GPU.
# The server runs in the background and listens on port 8000 (OpenAI-compatible API).
vllm-start() {
    # Check if already running
    if pgrep -f "vllm serve" > /dev/null 2>&1; then
        echo -e "\033[33mvLLM is already running:\033[0m"
        echo "  PID: $(pgrep -f 'vllm serve' | head -1)"
        echo "  Port: 8000"
        curl -s http://localhost:8000/v1/models 2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for m in data.get('data', []):
        print(f\"  Model: {m['id']}\")
except: pass" 2>/dev/null
        return 0
    fi

    local model="${1:-cyankiwi/gemma-4-31B-it-AWQ-4bit}"
    local max_len="${2:-16384}"

    echo -e "\033[34mStarting vLLM server...\033[0m"
    echo "  Model: $model"
    echo "  Max context: $max_len tokens"
    echo "  Port: 8000"
    echo ""

    conda run --no-banner -n vllm_env vllm serve "$model" \
        --max-model-len "$max_len" \
        --gpu-memory-utilization 0.90 \
        --enable-auto-tool-choice \
        --reasoning-parser gemma4 \
        --tool-call-parser gemma4 \
        --limit-mm-per-prompt '{"image": 4, "audio": 1}' \
        --async-scheduling \
        --host 0.0.0.0 \
        --port 8000 \
        > /tmp/vllm-server.log 2>&1 &

    local pid=$!
    echo "  Background PID: $pid"
    echo "  Log: /tmp/vllm-server.log"
    echo ""
    echo -e "\033[33mServer is starting up (CUDA kernel compilation may take a few minutes)...\033[0m"
    echo "  Check status:  vllm-status"
    echo "  View logs:     tail -f /tmp/vllm-server.log"
}

# Stop the vLLM inference server.
vllm-stop() {
    local pids
    pids=$(pgrep -f "vllm serve" 2>/dev/null)
    if [ -z "$pids" ]; then
        echo -e "\033[33mvLLM is not running.\033[0m"
        return 0
    fi

    echo -e "\033[34mStopping vLLM server...\033[0m"
    # Kill the main vllm process — child processes (EngineCore) will follow
    pkill -f "vllm serve" 2>/dev/null
    sleep 2

    # Make sure engine core processes are also gone
    pkill -f "VLLM::EngineCore" 2>/dev/null

    echo -e "\033[32m✓ vLLM server stopped.\033[0m"
}

# Check vLLM server status.
vllm-status() {
    if ! pgrep -f "vllm serve" > /dev/null 2>&1; then
        echo -e "\033[31mvLLM is not running.\033[0m"
        return 1
    fi

    echo -e "\033[32mvLLM is running\033[0m"
    echo "  PID: $(pgrep -f 'vllm serve' | head -1)"

    # Check if API is responsive
    local health
    health=$(curl -s --max-time 3 http://localhost:8000/health 2>/dev/null)
    if [ -n "$health" ]; then
        echo -e "  API: \033[32mready\033[0m (port 8000)"
        curl -s http://localhost:8000/v1/models 2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for m in data.get('data', []):
        print(f\"  Model: {m['id']}\")
except: pass" 2>/dev/null
    else
        echo -e "  API: \033[33mstarting up...\033[0m (not ready yet)"
    fi

    # GPU memory
    nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader 2>/dev/null | head -1 | \
        awk -F', ' '{print "  GPU memory: " $1 " / " $2}'
}

# Stop Bizon API server.
bizonapp-stop() {
    echo -e "\033[34mStopping Bizon API server...\033[0m"
    sudo systemctl stop bizon-api.service
    echo -e "\033[32m✓ Bizon API server stopped.\033[0m"
    echo ""
    echo "Service stopped:"
    echo "  - bizon-api (REST API, port 4000)"
    echo ""
    echo "Run 'bizonapp-start' to restart it."
}

# Start Bizon API server.
bizonapp-start() {
    echo -e "\033[34mStarting Bizon API server...\033[0m"
    sudo systemctl start bizon-api.service
    echo -e "\033[32m✓ Bizon API server started.\033[0m"
    echo ""
    bizonapp-status
}

# Show Bizon API server status.
bizonapp-status() {
    echo -e "\033[34m── Bizon API Server ──\033[0m"
    local state
    state=$(systemctl is-active bizon-api 2>/dev/null)
    if [ "$state" = "active" ]; then
        echo -e "  bizon-api: \033[32m$state\033[0m (port 4000)"
    else
        echo -e "  bizon-api: \033[31m$state\033[0m"
    fi
    echo ""

    # Show AI backend availability
    echo -e "\033[34m── AI Backends ──\033[0m"
    curl -s --max-time 3 http://localhost:4000/api/diagnostic/backends 2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for bid, info in data.get('backends', {}).items():
        status = '\033[32mavailable\033[0m' if info['available'] else '\033[31munavailable\033[0m'
        models = ', '.join(info.get('models', [])) or 'none'
        print(f'  {bid}: {status}  ({models})')
except:
    print('  (Bizon API not running — cannot check backends)')
" 2>/dev/null
}

# ─── Bizon MCP Server Guide ─────────────────────────────────────────────────
# Prints instructions for connecting the Bizon workstation MCP server
# to Claude Code, Claude Desktop, Cursor, Windsurf, or any MCP-compatible AI.
bizon-mcp-help() {
    cat << 'MCPHELP'

╔══════════════════════════════════════════════════════════════════╗
║              BIZON MCP SERVER — CONNECTION GUIDE                ║
╚══════════════════════════════════════════════════════════════════╝

  The Bizon MCP (Model Context Protocol) server lets any AI that
  supports MCP directly control and monitor this workstation.

  Server location:  /opt/bizon-api-server/mcp-server.js
  Transports:       stdio (local) or SSE (network, port 4001)

  ── AVAILABLE MCP TOOLS ──────────────────────────────────────

  get_system_info       Basic system info (hostname, CPU, GPU, RAM)
  get_detailed_specs    Full hardware specifications
  get_gpu_status        Real-time GPU metrics (temp, power, clocks)
  get_system_health     CPU, memory, swap, temps, disk, cooling
  run_command           Execute any shell command via SSH
  run_sudo_command      Execute privileged commands with sudo
  set_gpu_power_limit   Set GPU TDP in watts
  set_fan_speed         Set GPU fan speed (0-100%)
  reboot_machine        Reboot the workstation
  shutdown_machine      Shut down the workstation
  get_processes         List processes by CPU/memory usage
  get_gpu_processes     List GPU-accelerated processes
  get_disk_usage        Disk usage and block devices
  get_network_info      Network interfaces, routes, DNS
  manage_service        Start/stop/restart systemd services
  install_package       Install apt packages
  read_file             Read file contents
  write_file            Write/append to files

  ── 1. CLAUDE CODE (CLI) ─────────────────────────────────────

  Add to your project's .mcp.json or ~/.claude/mcp.json:

    {
      "mcpServers": {
        "bizon-workstation": {
          "command": "node",
          "args": ["mcp-server.js", "--transport", "stdio"],
          "cwd": "/opt/bizon-api-server"
        }
      }
    }

  Then restart Claude Code. The tools will appear automatically.
  Claude can then run commands, check GPU status, manage services,
  and more — directly on this workstation.

  ── 2. CLAUDE DESKTOP APP ────────────────────────────────────

  Edit ~/Library/Application Support/Claude/claude_desktop_config.json
  (macOS) or %APPDATA%\Claude\claude_desktop_config.json (Windows):

    {
      "mcpServers": {
        "bizon-workstation": {
          "command": "node",
          "args": ["mcp-server.js", "--transport", "stdio"],
          "cwd": "/opt/bizon-api-server"
        }
      }
    }

  Note: This only works if Claude Desktop is running on the same
  machine as the Bizon workstation (localhost SSH).

  ── 3. REMOTE ACCESS (SSE transport) ─────────────────────────

  For remote AI integrations (e.g., from another machine):

    # Start SSE server on port 4001:
    cd /opt/bizon-api-server && npm run mcp:sse

    # Or run both REST API + MCP SSE together:
    npm run start:all

  The SSE endpoint is: http://<workstation-ip>:4001/sse

  ── 4. CURSOR / WINDSURF / OTHER MCP CLIENTS ─────────────────

  Most MCP clients use the same config format. Add to their
  MCP configuration file:

    {
      "mcpServers": {
        "bizon-workstation": {
          "command": "node",
          "args": ["/opt/bizon-api-server/mcp-server.js", "--transport", "stdio"]
        }
      }
    }

  ── 5. QUICK TEST ─────────────────────────────────────────────

  Test the MCP server directly:

    cd /opt/bizon-api-server
    echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | node mcp-server.js --transport stdio

  ── TIPS ──────────────────────────────────────────────────────

  • All MCP tools require SSH credentials (username/password)
    which the AI will provide per-call.
  • For sudo operations, the AI must also provide a sudoPassword.
  • The MCP server connects to localhost:22 (SSH), so it must
    run on the same machine as the workstation.
  • For security, only expose the SSE endpoint on trusted networks.

MCPHELP
}

# >>> Conda Initialization >>>
__conda_setup="$('/home/bizon/anaconda3/bin/conda' 'shell.bash' 'hook' 2> /dev/null)"
if [ $? -eq 0 ]; then
    eval "$__conda_setup"
else
    if [ -f "/home/bizon/anaconda3/etc/profile.d/conda.sh" ]; then
        . "/home/bizon/anaconda3/etc/profile.d/conda.sh"
    else
        export PATH="/home/bizon/anaconda3/bin:$PATH"
    fi
fi
unset __conda_setup
# <<< Conda Initialization <<<

alias ipmiview='java -jar /bin/ipmiview/IPMIView20.jar'

# Added by LM Studio CLI (lms)
export PATH="$PATH:/home/bizon/.cache/lm-studio/bin"

# Enable bash completion
if [ -f /etc/bash_completion ]; then
    . /etc/bash_completion
fi
# Bizon API server update
alias bizonapp-update='sudo /opt/bizon-api-server/update_services.sh'
export PATH="$HOME/.local/bin:$PATH"
