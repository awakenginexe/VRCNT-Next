# Build VRCT (Windows x64) with Parakeet + Vosk additions

This document covers building the modified VRCT (with Parakeet ONNX and Vosk ONNX engines added) into a Windows x64 installer.

## Prerequisites

### 1. Visual Studio Build Tools
Download: https://visualstudio.microsoft.com/visual-cpp-build-tools/

Install with workload **"Desktop development with C++"**:
- MSVC v143 (or latest) - VS 2022 C++ x64/x86 build tools
- Windows 11 SDK (or 10 SDK)
- C++ CMake tools for Windows

### 2. Rust toolchain (for Tauri)

```powershell
winget install Rustlang.Rustup
rustup default stable-x86_64-pc-windows-msvc
rustc --version  # confirm
```

### 3. Python 3.11 (DO NOT use 3.13)

Pinned packages such as `torch==2.7.0`, `numpy==1.26.4`, `ctranslate2==4.6.0`, `PyAudioWPatch==0.2.12.6` ship wheels only up to Python 3.12. Python 3.13 will break the install step.

```powershell
winget install Python.Python.3.11
py -3.11 --version  # confirm
```

### 4. Node.js 18+ (already have v24)

```powershell
node --version  # >= 18
```

### 5. (CUDA build only) NVIDIA CUDA 12.x + cuDNN

Required only if you build the GPU variant (`build-cuda`).

---

## Build steps

All commands run from the VRCT repo root in PowerShell.

### Step 1 — Install Node dependencies

```powershell
npm install
```

Pulls in Tauri CLI as a dev dependency.

### Step 2 — Force install.bat to use Python 3.11

Edit `bat/install.bat` and replace every `python -m venv` line with:

```bat
py -3.11 -m venv .venv
...
py -3.11 -m venv .venv_cuda
```

(or temporarily put a `python.exe` 3.11 first on PATH)

### Step 3 — Create venvs and install Python deps

```powershell
npm run setup-python
```

This creates `.venv\` (CPU) and `.venv_cuda\` (GPU) and runs `pip install -r requirements.txt` / `requirements_cuda.txt` into each.

**New dependencies added in this build:**
- `onnxruntime==1.19.2` (CPU) / `onnxruntime-gpu==1.19.2` (CUDA)
- `sherpa-onnx==1.10.30`
- `vosk==0.3.45`

⚠️ If `sherpa-onnx` wheel is missing for Python 3.11, fall back to:
```powershell
.venv\Scripts\pip install sherpa-onnx==1.10.20
```

### Step 4 — Build CPU variant

```powershell
npm run build
```

Pipeline:
1. `task-kill` — kill stale VRCT processes
2. `clean` — wipe `src-tauri/bin` and `dist`
3. `update-version` — bump version in `tauri.conf.json`
4. `build-python` → `pyinstaller spec/backend.spec` — bundles Python sidecar to `src-tauri/bin/VRCT-sidecar-x86_64-pc-windows-msvc.exe`
5. `vite-build` — builds JS UI to `dist/`
6. `tauri build` — Cargo compiles Rust shell, then NSIS packages everything

Output: `src-tauri/target/release/bundle/nsis/VRCT_<version>_x64-setup.exe`

### Step 5 — (Optional) Build CUDA variant

```powershell
npm run build-cuda
```

Same pipeline but uses `.venv_cuda` and `spec/backend_cuda.spec`. Produces a GPU-accelerated build.

### Step 6 — (Optional) Release ZIP

```powershell
npm run release        # CPU
npm run release-cuda   # CUDA
npm run release-all    # both
```

Produces `VRCT.zip` / `VRCT_cuda.zip` at the repo root.

---

## Post-build verification checklist

After the installer runs, check:

- [ ] App launches without console errors
- [ ] Settings → Transcription shows **4 engine options**: Google, Whisper, Parakeet, Vosk
- [ ] Selecting **Whisper** still works as before (regression check)
- [ ] Selecting **Vosk** shows model picker with `small-en`, `large-en`, `small-ja`, etc., each with capacity badge (e.g. `80 MB / ~200 MB RAM`)
- [ ] Selecting **Parakeet** shows model picker with VRAM badges (e.g. `~2 GB VRAM`)
- [ ] Downloading a Vosk model writes to `weights/vosk/<key>/`
- [ ] Downloading a Parakeet model writes to `weights/parakeet/<key>/`
- [ ] After download, switching to Vosk/Parakeet and speaking produces transcripts in VRChat chatbox
- [ ] Network OSC packets still emitted (check Wireshark `udp.port == 9000`)

---

## Known gaps in this build

1. **Frontend hooks incomplete** — `useTranscription` (in `src-ui/logics/configs/useTranscription`) lacks setters for `setSelectedVoskWeightType`, `setSelectedParakeetWeightType`, and the corresponding status hooks. Existing Whisper hooks are the template to copy.

2. **Backend ↔ frontend routing** — `src-ui/logics/useReceiveRoutes.js` needs entries for:
   - `download_progress_vosk_weight`
   - `downloaded_vosk_weight`
   - `download_progress_parakeet_weight`
   - `downloaded_parakeet_weight`

3. **i18n strings** — UI labels for Vosk/Parakeet boxes are hardcoded English. Add to `locales/en.yml`, `ja.yml`, `ko.yml`, `zh-Hant.yml`, `zh-Hans.yml`.

4. **Parakeet decoder is minimal** — `transcription_parakeet.py::ParakeetRecognizer.transcribe` does a generic argmax+CTC-style merge. For best accuracy, tune against the specific ONNX export's output graph (TDT vs RNNT vs CTC heads).

5. **Compute device locking** — UI should auto-lock CPU when Vosk is selected, lock GPU when Parakeet is selected. Currently no enforcement.

6. **Pre-existing spec bug fixed** — `spec/backend_cuda.spec` previously referenced `.venv/Lib/site-packages/hf_xet`; corrected to `.venv_cuda/` in this build.

---

## Troubleshooting

### `pip install sherpa-onnx` fails
Fall back to an older version that has Python 3.11 wheels:
```powershell
.venv\Scripts\pip install sherpa-onnx==1.10.20
```
Then update `requirements.txt` accordingly.

### `pyinstaller` fails with "module not found: sherpa_onnx"
Verify the venv is activated, then check `.venv\Lib\site-packages\sherpa_onnx\` exists. The spec adds it as both `datas` and `hiddenimports`.

### `tauri build` fails with "linker `link.exe` not found"
MSVC Build Tools not installed correctly. Reinstall VS Build Tools with the C++ workload.

### NSIS installer build hangs
Tauri downloads NSIS plugins on first build. Ensure internet access and rerun.

### Final exe is huge (>500 MB)
Expected — bundling PyTorch + ONNX Runtime + CUDA libs (for CUDA build) easily reaches 1+ GB. UPX is enabled in spec but only compresses parts. For smaller bundles, switch Parakeet to CPU ONNX Runtime or drop unused engines.
