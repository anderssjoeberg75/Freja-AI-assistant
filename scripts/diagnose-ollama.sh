#!/usr/bin/env bash
# Reports why Ollama is (or is not) using the GPU on the machine that runs it.
#
# Run it ON the Ollama host - everything here reads local state that the HTTP API does not
# expose. It only reads: no package is installed, no service is restarted, no file is
# changed. Paste the output back into the Freja session and the fix follows from it.
#
#   bash scripts/diagnose-ollama.sh
#
# The short version of what it is looking for: Ollama picks its compute backend at startup
# and logs the decision. If the NVIDIA driver is missing, or Ollama was installed before the
# driver was, or the service unit pins it to the CPU, it quietly runs on the CPU forever -
# roughly 15-20x slower - with no error anywhere except that startup line.

set -uo pipefail

section() { printf '\n=== %s ===\n' "$1"; }

section "Host"
uname -a
[ -r /etc/os-release ] && . /etc/os-release && echo "distro: ${PRETTY_NAME:-unknown}"

section "NVIDIA driver (nvidia-smi)"
if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used,memory.free \
               --format=csv 2>&1 || echo "nvidia-smi is installed but failed to run - the driver is not working."
else
    echo "NOT FOUND. Without the NVIDIA driver and nvidia-smi, Ollama has no GPU to use."
    echo "  Ubuntu/Debian: sudo ubuntu-drivers install   (or: sudo apt install nvidia-driver-<version>)"
    echo "  Reboot afterwards, then re-run this script."
fi

section "Is the GPU visible as a device?"
ls -l /dev/nvidia* 2>/dev/null || echo "No /dev/nvidia* devices. Driver not loaded, or this is a container/VM without GPU passthrough."

section "Ollama version and service state"
command -v ollama >/dev/null 2>&1 && ollama --version 2>&1 || echo "the ollama CLI is not on PATH"
systemctl is-active ollama 2>/dev/null || echo "(the ollama service is not managed by systemd under that name)"

section "Service unit - environment overrides"
# A stray Environment= line pinning the CPU is a common cause: someone sets it while
# debugging and it survives every later fix.
systemctl cat ollama 2>/dev/null | grep -E '^(Environment|ExecStart|User|Group)' || echo "(no unit found)"

section "Currently loaded models (0 B VRAM here means CPU inference)"
curl -s -m 5 http://127.0.0.1:11434/api/ps || echo "Ollama's API did not answer on 127.0.0.1:11434"

section "What Ollama detected at startup - THE decisive line"
# "inference compute ... library=cuda" means the GPU is in use.
# "no compatible GPUs were discovered" / "library=cpu" means it is not.
journalctl -u ollama --no-pager -n 400 2>/dev/null \
    | grep -iE "inference compute|gpu|cuda|rocm|vram|library" \
    | tail -30 \
    || echo "No journal entries. If Ollama runs in Docker: docker logs <container> 2>&1 | grep -i gpu"

section "Verdict"
cat <<'EOF'
Read the section above:

  * "library=cuda" plus a VRAM figure   -> the GPU is working. If inference is still slow,
                                           the context window is too large to fit; lower
                                           "Ollama Context Window" in the admin portal.
  * "no compatible GPUs were discovered" -> the driver is missing/broken, or Ollama was
                                           installed before the driver. Install the driver,
                                           reboot, then reinstall Ollama so it picks up CUDA:
                                             curl -fsSL https://ollama.com/install.sh | sh
                                             sudo systemctl restart ollama
  * "library=cpu" with a working nvidia-smi -> something is forcing the CPU. Look for
                                           OLLAMA_LLM_LIBRARY or CUDA_VISIBLE_DEVICES in the
                                           unit section above and remove it:
                                             sudo systemctl edit ollama
                                             sudo systemctl restart ollama
EOF
