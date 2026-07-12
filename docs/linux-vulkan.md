# Linux GPU dictation — whisper.cpp Vulkan + GNOME Wayland

Runbook for running Whisper Toggle on Linux with **GPU-accelerated transcription
on Intel/AMD integrated graphics** (validated on an Intel Iris Xe, Ubuntu 26.04,
GNOME Wayland — ~13x realtime, proper-cased output). faster-whisper/CTranslate2
cannot target Intel/AMD GPUs, so the `vulkan` device routes to whisper.cpp.

## Architecture on Linux

```
Ctrl+`  ->  dictate-toggle.sh  ->  pw-record (mic)  ->  POST /v1/audio/transcriptions
                                                             |
                     local FastAPI (app.py), WHISPER_API_DEVICE=vulkan
                                                             |
                                          whisper_toggle/whispercpp.py -> whisper-cli (Vulkan / iGPU)
                                                             |
                        insert_text(): wl-copy (clipboard) + ydotool type (GNOME) / xdotool (X11)
```

Batch is the path on Linux (accurate + simple). The API device is env-selected:
`WHISPER_API_DEVICE=vulkan` → whisper.cpp; `cpu`/`cuda` → faster-whisper.

## 1. Python engine (no sudo)

Ubuntu 26.04 ships Python 3.14, which is too new for the faster-whisper/ctranslate2
wheels, so use a self-contained build:

```bash
mkdir -p ~/whisper-toggle && cd ~/whisper-toggle
# portable CPython 3.11 (python-build-standalone), then:
./python/bin/pip install faster-whisper ctranslate2 fastapi "uvicorn[standard]" \
    python-multipart numpy websockets httpx sherpa-onnx
git clone https://github.com/tolewis/Whisper-Toggle.git repo   # PYTHONPATH=repo
```

## 2. whisper.cpp with Vulkan (no sudo for the build itself)

System deps (sudo, once): `build-essential cmake libvulkan-dev glslc vulkan-tools`
and the Vulkan ICD (`mesa-vulkan-drivers` — usually already present; check
`vulkaninfo --summary`). SPIRV-Headers is also required and can be installed
**without sudo**:

```bash
cd ~/whisper-toggle
git clone https://github.com/KhronosGroup/SPIRV-Headers
cmake -S SPIRV-Headers -B SPIRV-Headers/build -DCMAKE_INSTALL_PREFIX=$HOME/whisper-toggle/local
cmake --build SPIRV-Headers/build --target install

git clone https://github.com/ggml-org/whisper.cpp
cd whisper.cpp
cmake -B build -DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_TESTS=OFF \
      -DCMAKE_PREFIX_PATH=$HOME/whisper-toggle/local
CPATH=$HOME/whisper-toggle/local/include cmake --build build -j   # CPATH -> spirv/unified1/spirv.hpp
# model:
curl -L https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin \
     -o models/ggml-base.en.bin
# verify GPU:  ./build/bin/whisper-cli -m models/ggml-base.en.bin -f samples/jfk.wav -nt
```

## 3. API as a user systemd service

`~/.config/systemd/user/whisper-api.service` (see `linux/whisper-api.service`):
set `Environment=WHISPER_API_DEVICE=vulkan`, `WHISPER_CPP_BIN=…/whisper-cli`,
`WHISPER_CPP_MODEL=…/ggml-base.en.bin`. Then
`systemctl --user enable --now whisper-api`. Speed vs accuracy: swap the ggml
model (tiny/base/small). Garbled iGPU output? set `WHISPER_VK_DISABLE_F16=1`.

## 4. Auto-type on GNOME Wayland — ydotool (NOT wtype)

GNOME/Mutter does **not** implement the virtual-keyboard protocol `wtype` needs,
so `wtype` silently no-ops on GNOME. Use `ydotool` (kernel `uinput`):

```bash
sudo apt install -y ydotool
echo 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' \
    | sudo tee /etc/udev/rules.d/60-uinput.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo usermod -aG input $USER            # then log out/in
```

Run `ydotoold` from the graphical session (its context has the `input` group; the
`systemd --user` manager may predate the group change until a reboot). A login
autostart is the simplest: `~/.config/autostart/ydotoold.desktop` running
`ydotoold -p /run/user/$UID/.ydotool_socket -P 0660`. `dictate-toggle.sh` then
uses `ydotool type` (fast key-delay ~5ms/char), keeping the clipboard as a
manual-paste fallback.

## 5. Hotkey (GNOME custom shortcut → Ctrl+`)

```bash
B=org.gnome.settings-daemon.plugins.media-keys
K=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/whisper/
gsettings set "$B" custom-keybindings "['$K']"
gsettings set "$B.custom-keybinding:$K" name "Whisper Toggle"
gsettings set "$B.custom-keybinding:$K" command "$HOME/bin/whisper-dictate"
gsettings set "$B.custom-keybinding:$K" binding "<Primary>grave"
```

`~/bin/whisper-dictate` exports `WHISPER_STREAMING=0` (batch) and execs
`linux/dictate-toggle.sh`.
