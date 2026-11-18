#!/usr/bin/env bash
# Candidate installer: no drivers, firmware, ECC, power limits or existing GPU jobs are changed.
set -euo pipefail
if [[ $EUID -ne 0 || $# -ne 1 ]]; then echo "Usage: sudo bash gpu-qa/install.sh OPERATOR_USERNAME" >&2; exit 2; fi
operator=$1
id "$operator" >/dev/null
[[ $(uname -s) == Linux ]] || { echo 'Linux/systemd required'; exit 2; }
[[ ! -e /opt/bizon-gpu-qa && ! -e /etc/bizon-gpu-qa ]] || { echo 'Existing install found. Do not replace code during runs; see upgrade instructions.'; exit 2; }
for cmd in python3 git cmake make g++ nvidia-smi nvcc systemctl runuser visudo; do command -v "$cmd" >/dev/null || { echo "Missing prerequisite: $cmd"; exit 2; }; done
python3 - <<'PY'
import re,subprocess,sys
assert sys.version_info >= (3,10), 'Python 3.10+ required'
v=re.search(r'release (\d+)\.(\d+)',subprocess.check_output(['nvcc','--version'],text=True))
assert v and tuple(map(int,v.groups())) >= (12,9), 'Existing CUDA toolkit 12.9+ required'
PY
src=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
base=/opt/bizon-gpu-qa
state=/var/lib/bizon-gpu-qa
getent group bizon-gpu-qa >/dev/null || groupadd --system bizon-gpu-qa
id bizon-gpu-qa >/dev/null 2>&1 || useradd --system --gid bizon-gpu-qa --home-dir "$state" --shell /usr/sbin/nologin bizon-gpu-qa
for group in video render systemd-journal; do getent group "$group" >/dev/null || groupadd --system "$group"; usermod -aG "$group" bizon-gpu-qa; done
usermod -aG bizon-gpu-qa "$operator"
install -d -m 0755 "$base" /etc/bizon-gpu-qa /usr/local/libexec
install -d -o root -g bizon-gpu-qa -m 0750 "$state" "$state/runs"
install -d -o bizon-gpu-qa -g bizon-gpu-qa -m 2770 "$state/locks" "$state/cache"
install -d -m 0755 "$base/gpuqa"
install -m 0644 "$src"/gpuqa/*.py "$base/gpuqa/"
install -m 0644 "$src/memory_test.cu" "$base/"
python3 -m venv "$base/control"
"$base/control/bin/python" -m pip install --disable-pip-version-check 'reportlab==4.4.10'
python3 -m venv "$base/worker"
"$base/worker/bin/python" -m pip install --disable-pip-version-check 'torch==2.10.0' --index-url https://download.pytorch.org/whl/cu130
"$base/worker/bin/python" -m pip install --disable-pip-version-check 'transformers==5.5.4' 'safetensors==0.7.0'
# Immutable upstream source revisions. Store full transitive runtime inventory below.
git clone https://github.com/wilicc/gpu-burn.git "$base/gpu-burn"
git -C "$base/gpu-burn" checkout --detach 0d19c94dcb2e9b6858b7586a4c327674cd67113b
cuda_root=$(dirname "$(dirname "$(readlink -f "$(command -v nvcc)")")")
make -C "$base/gpu-burn" -j2 COMPUTE=75 "CUDAPATH=$cuda_root"
git clone https://github.com/NVIDIA/nvbandwidth.git "$base/nvbandwidth"
git -C "$base/nvbandwidth" checkout --detach 201eaf7f25eda954d08a7c4fed9d8325e3c32ddf
cmake -S "$base/nvbandwidth" -B "$base/nvbandwidth/build" -DCMAKE_CUDA_ARCHITECTURES=75 -DCMAKE_BUILD_TYPE=Release
cmake --build "$base/nvbandwidth/build" -j2
nvcc -O3 -std=c++14 -gencode arch=compute_75,code=compute_75 "$base/memory_test.cu" -o "$base/memory-test"
"$base/worker/bin/python" - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download('Qwen/Qwen3-0.6B',revision='c1899de289a04d12100db370d81485cdf75e47ca',local_dir='/opt/bizon-gpu-qa/model',allow_patterns=['*.json','*.safetensors','*.txt','*.model','*.jinja'])
PY
"$base/control/bin/python" - <<'PY'
import hashlib,json,pathlib,subprocess
b=pathlib.Path('/opt/bizon-gpu-qa')
files=[b/'memory-test',b/'gpu-burn/gpu_burn',b/'gpu-burn/compare.fatbin',b/'nvbandwidth/build/nvbandwidth']+sorted((b/'model').glob('*.safetensors'))
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for chunk in iter(lambda:f.read(8*1024*1024),b''):h.update(chunk)
 return h.hexdigest()
manifest={'files':{str(p):sha(p) for p in files},'worker_packages':subprocess.check_output([str(b/'worker/bin/python'),'-m','pip','freeze'],text=True),'control_packages':subprocess.check_output([str(b/'control/bin/python'),'-m','pip','freeze'],text=True),'nvcc':subprocess.check_output(['nvcc','--version'],text=True),'upstream':{'gpu_burn':'0d19c94dcb2e9b6858b7586a4c327674cd67113b','nvbandwidth':'201eaf7f25eda954d08a7c4fed9d8325e3c32ddf'}}
raw=json.dumps(manifest,sort_keys=True,indent=2);(b/'software-manifest.json').write_text(raw)
c={'python':str(b/'worker/bin/python'),'model_path':str(b/'model'),'model_revision':'c1899de289a04d12100db370d81485cdf75e47ca','gpu_burn':str(b/'gpu-burn/gpu_burn'),'nvbandwidth':str(b/'nvbandwidth/build/nvbandwidth'),'memory_test':str(b/'memory-test'),'memory_fraction':.90,'compute_sanitizer':str(pathlib.Path(subprocess.check_output(['which','nvcc'],text=True).strip()).resolve().parent/'compute-sanitizer'),'dependency_fingerprint':hashlib.sha256(raw.encode()).hexdigest(),'manifest':str(b/'software-manifest.json'),'qualified_configurations':[]}
pathlib.Path('/etc/bizon-gpu-qa/config.json').write_text(json.dumps(c,indent=2)+'\n')
PY
chown -R root:root "$base" /etc/bizon-gpu-qa
chmod -R go-w "$base" /etc/bizon-gpu-qa
install -m 0755 "$src/gpu-qa-start" /usr/local/libexec/gpu-qa-start
cat > /usr/local/bin/gpu-qa <<'EOF'
#!/bin/sh
export PYTHONPATH=/opt/bizon-gpu-qa
export PYTHONDONTWRITEBYTECODE=1
exec /opt/bizon-gpu-qa/control/bin/python -m gpuqa.cli "$@"
EOF
chmod 0755 /usr/local/bin/gpu-qa
install -m 0644 "$src/bizon-gpu-qa@.service" /etc/systemd/system/
printf '%%bizon-gpu-qa ALL=(root) NOPASSWD: /usr/local/libexec/gpu-qa-start\n' > /etc/sudoers.d/bizon-gpu-qa
chmod 0440 /etc/sudoers.d/bizon-gpu-qa
visudo -cf /etc/sudoers.d/bizon-gpu-qa
systemctl daemon-reload
# Append an idempotent help extension; preserve the operator's existing aliases/functions.
install -m 0644 "$src/bizonhelp-gpu-qa.bash" "$base/"
operator_home=$(getent passwd "$operator" | cut -d: -f6)
runuser -u "$operator" -- python3 - "$operator_home/.bashrc" <<'PY'
import pathlib,sys
p=pathlib.Path(sys.argv[1]);s=p.read_text() if p.exists() else ''
line='[ ! -r /opt/bizon-gpu-qa/bizonhelp-gpu-qa.bash ] || source /opt/bizon-gpu-qa/bizonhelp-gpu-qa.bash'
if line not in s:
 if p.exists():p.with_name('.bashrc.before-gpu-qa').write_text(s)
 p.write_text(s+'\n# BIZON GPU QA help extension\n'+line+'\n')
PY
echo 'Candidate installed. Log out/in to refresh group access, then run: gpu-qa doctor'
echo 'Hardware qualification is pending. Use gpu-qa validate --gpus all, then pilot profiles; no production approval is enabled.'
