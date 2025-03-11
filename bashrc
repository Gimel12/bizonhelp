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
echo Bizonhelp_3.1
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
echo
echo -e "\033[31;1m-----------------Beta Features----------------\033[0m"
echo -e ollama-install ----"\033[31;1mInstall Ollama for LLM interaction\033[0m"
echo -e lmstudio-install --"\033[31;1mInstall LM Studio (latest version)\033[0m"
echo -e lmstudio-launch ---"\033[31;1mLaunch LM Studio\033[0m"
echo -e cursor-launch -----"\033[31;1mLaunch Cursor AI coding assistant\033[0m"
echo -e raid-manager -----"\033[31;1mBizon Raid manager\033[0m"
echo -e nvme-stress -----"\033[31;1mBizon Nvme tool to stress the drives\033[0m"
echo'

# Nvme stress tool
alias nvme-stress='
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
