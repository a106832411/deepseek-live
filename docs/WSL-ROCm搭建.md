# WSL2 + ROCm GPU 环境搭建记录（2026-11-12）

> 目标：让 RX 7900 GRE 在 WSL2 里跑 ROCm PyTorch，给唱歌工坊 / 鲸live 的 RVC 翻唱加速。
> 这份文档记录**实际跑通**的步骤和踩过的坑（不是教程抄录）。

## 0. 结果速览

| 项目 | 结果 |
|---|---|
| WSL | 3.0.5（内核 6.18.58.1-microsoft-standard-WSL2） |
| 发行版 | Ubuntu 22.04（用 rootfs `wsl --import`，不走商店） |
| ROCm | 6.4（`amdgpu-install --usecase=rocm --no-dkms`） |
| `rocminfo` | `gfx1100` / `AMD Radeon RX 7900 GRE` ✅ |
| PyTorch | 2.8.0+rocm6.4（本地 wheel），`torch.cuda.is_available()=True` ✅ |
| GPU matmul | 2048×2048 正常 ✅ |
| demucs GPU | 90 秒音频 25 秒（同段 CPU 26 秒；demucs 太小，GPU 优势不明显） |
| RVC GPU | 待真实声线模型验证（CPU 是 5~15 分钟/首，GPU 预期快数倍） |

## 1. Windows 侧准备

1. 启用 WSL（家庭版也行），装 WSL 3.0.5：`wsl --install --no-distribution`（需要管理员，装完**重启**）
2. 下载 Ubuntu 22.04 base rootfs（约 28MB）：
   `https://mirrors.tuna.tsinghua.edu.cn/ubuntu-cdimage/ubuntu-base/releases/22.04/release/ubuntu-base-22.04.5-base-amd64.tar.gz`
3. 导入（不需要管理员）：
   ```powershell
   wsl --import Ubuntu-22.04 D:\wsl\Ubuntu-22.04 <rootfs.tar.gz> --version 2
   wsl -d Ubuntu-22.04 -u root -- uname -a
   ```
4. 提前下好（可选，省时间）：
   - ROCm 安装器：`https://repo.radeon.com/amdgpu-install/6.4/ubuntu/jammy/amdgpu-install_6.4.60400-1_all.deb`
   - torch/torchaudio ROCm 6.4 py310 轮子（SJTU 镜像，直链在 `/pytorch-wheels/rocm6.4/` 根目录下）：
     `torch-2.8.0+rocm6.4-cp310-cp310-manylinux_2_28_x86_64.whl`（3.44GB）、`torchaudio-...whl`
   - RVC 基础模型 `hubert_base.pt` / `rmvpe.pt`（hf-mirror 的 `lj1995/VoiceConversionWebUI`）

## 2. WSL 里装 ROCm（踩坑最多的一步）

```bash
# 2.1 apt 源先用 http（base rootfs 没有 ca-certificates，用 https 会报 cert 不受信任）
sed -i 's|http://archive.ubuntu.com|http://mirrors.tuna.tsinghua.edu.cn|g; ...' /etc/apt/sources.list
apt-get update
apt-get install -y ca-certificates curl iproute2 wget gnupg2 lsb-release libnuma-dev

# 2.2 装 amdgpu-install，然后只装 ROCm 用户态
apt-get install -y ./amdgpu-install_6.4.60400-1_all.deb
amdgpu-install --usecase=rocm --no-dkms -y      # WSL 里绝不能装 dkms
rocminfo | grep -E 'gfx|Marketing'              # 应看到 gfx1100 / RX 7900 GRE
```

> repo.radeon.com 从 WSL 直连可用；如果卡住再考虑代理（Clash 要开 Allow LAN，WSL 用宿主 IP:7890）。

## 3. Python + PyTorch ROCm

```bash
apt-get install -y python3.10 python3.10-venv python3-pip ffmpeg libsndfile1 git build-essential
python3.10 -m venv /opt/rvc && source /opt/rvc/bin/activate
pip install -U pip -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install filelock typing-extensions sympy networkx jinja2 fsspec mpmath
# 关键：--no-deps！torch 2.8 依赖 pytorch-triton-rocm==3.4.0，国内源没有，
# 正规装会被 pip 判定 torch 不满足，转而从 PyPI 拉 CUDA 版 torch（还会拖 nvidia-cudnn 553MB）
pip install --no-deps /path/to/torch-2.8.0+rocm6.4-*.whl /path/to/torchaudio-2.8.0+rocm6.4-*.whl
# 装完把 torch 元数据里的 triton 依赖删掉，否则后续任何 pip install 都会想重装 torch
sed -i '/pytorch-triton-rocm/d' /opt/rvc/lib/python3.10/site-packages/torch-*.dist-info/METADATA
pip install "numpy==1.23.5"   # 实测 2.2.6 也能跑 rvc，但 1.23.5 和 Windows CPU 环境一致
```

## 4. ★ 让 torch 在 WSL 里看见显卡（最关键的两个环境变量）

WSL 没有 `/dev/kfd`（AMD GPU 走 `/dev/dxg` + DXG 虚拟化）。torch 自带的 `libhsa-runtime64.so` 不认 DXG，
表现为 `torch.cuda.is_available()=False` / `No HIP GPUs are available`，而 `rocminfo` 却正常。

```bash
export HSA_ENABLE_DXG_DETECTION=1
export LD_PRELOAD=/opt/rocm/lib/libhsa-runtime64.so.1   # 用系统 ROCm 的 WSL 版 HSA 运行时
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
# → True AMD Radeon RX 7900 GRE
```

已落盘到：
- `/opt/rvc/gpu-env.sh`（`source` 即可）
- `/etc/profile.d/rocm-wsl.sh`（登录 shell 自动生效）
- 鲸live `sing.rvc.dxg` / `sing.rvc.ld_preload`、唱歌工坊桥的 `SONG_WSL_DXG` / `SONG_WSL_LD_PRELOAD` 也都内置了

## 5. RVC 依赖 + torch.load 兼容（torch≥2.6 的坑）

```bash
pip install demucs==4.0.1 librosa soundfile scipy av omegaconf loguru ffmpeg-python sounddevice \
  pyworld praat-parselmouth faiss-cpu torchcrepe julius lameenc openunmix einops \
  dora-search treetable submitit cloudpickle retrying
pip install --no-deps rvc-python==0.1.5
```

torch 2.6+ 把 `torch.load` 默认 `weights_only` 改成 `True`，rvc-python 0.1.5 里 6 处调用没传这个参数，
会报 `Weights only load failed ... Unsupported global`。已直接改源码补 `weights_only=False`：

```bash
SP=/opt/rvc/lib/python3.10/site-packages
sed -i 's/torch.load(sid, map_location="cpu")/torch.load(sid, map_location="cpu", weights_only=False)/' $SP/rvc_python/modules/vc/modules.py
sed -i 's/torch.load(model_path, map_location="cpu")/torch.load(model_path, map_location="cpu", weights_only=False)/' $SP/rvc_python/lib/rmvpe.py
sed -i 's/torch.load(model_path, map_location=device)/torch.load(model_path, map_location=device, weights_only=False)/' $SP/rvc_python/lib/jit/get_rmvpe.py
# jit/get_synthesizer.py、jit/__init__.py、modules/onnx/export.py 同理
```

> 注意：venv 里的 `sitecustomize.py` 打补丁**不生效**——Python 会优先导入 `/usr/lib/python3.10/sitecustomize.py`，
> 所以直接改源码最稳。

fairseq：**不用装**。唱歌工坊自带 `compat/fairseq` shim（用 torchaudio HuBERT 加载 hubert_base.pt），
运行时 `PYTHONPATH=/opt/rvc/compat` 即可（鲸live 和唱歌工坊桥都内置了）。

## 6. 声线表路径（容易忽略）

`voices.json` 里 `pth` / `index` 是 **Windows 路径**，WSL 里打不开。必须在跑之前转成 `/mnt/x/...`。
鲸live 的 RVC 引擎和唱歌工坊桥都会自动生成一份 `/mnt` 化副本再传进去。

## 7. 当前状态 / 下一步

- 环境已就绪：`/opt/rvc/bin/python` + GPU 可用 + demucs/rvc-python + 基础模型
- 等用户的 **RVC 声线模型**（`.pth` + 可选 `.index`）写进 voices.json，即可：
  1. 唱歌工坊 UI 做歌（插件「配置 → Python」已指向 WSL 桥 `song-studio-wsl.exe`）
  2. 鲸live 直播点歌：`sing.engine="rvc"` + `sing.rvc.mode="wsl"`
- demucs 在 GPU 上和 CPU 差不多（25s vs 26s / 90s 音频），真正提速的是 RVC 换声那一步；
  如果发现 GPU 整体更慢，可以把 demucs 单独放 CPU（后续可做成可配置）。

## 8. RVC 在 WSL GPU 上会 segfault（重要结论，2026-11-12 补充）

- demucs 在 WSL GPU 上正常（90 秒音频 25 秒）
- 但 **RVC 推理（rvc_python）在 WSL + ROCm 下会段错误**，崩在 `torch/nn/modules/conv.py _conv_forward`
  （`rvc_python/lib/infer_pack/modules.py` ResBlock）。已尝试均无效：
  `RVC_HALF=0`（fp32）、`RVC_NOCUDNN=1`（禁用 cudnn/MIOpen 路径）、`MIOPEN_FIND_MODE=FAST/NORMAL/HYBRID`、
  换 f0method（rmvpe/crepe/pm）；单独测 conv1d 各种 kernel/dilation、fft/stft、LSTM/GRU 都正常，
  说明是模型上下文里的某种组合触发 DXG 后端崩溃，继续排查成本高。
- **结论：RVC 走 Windows CPU。** 实测（Windows 的 torch 2.0 + MKL，GSV runtime）：
  6 秒音频 RVC 推理 4.9 秒、完整 cover.py（20 秒音频：分离 19s + 换声 20s + 混音 1s）= 40 秒，
  一首 4 分钟的歌约 6~8 分钟，完全可以接受。
- WSL GPU 保留给 demucs / 未来修好 RVC 时再用；鲸live 和唱歌工坊默认配置已切到 `mode=local, device=cpu:0`。
- 复现环境：torch 2.8.0+rocm6.4、ROCm 6.4、WSL 3.0.5、驱动 32.0.32015.7029；如果以后升级 torch/ROCm 可以再试 GPU RVC。

- 也试过换 torch 版本：**torch 2.7.1+rocm6.3 在 WSL 里 `torch.cuda.is_available()=False`**
  （ROCm 6.3 的 wheel 与系统 ROCm 6.4 运行时不匹配，连 GPU 都看不到，RVC 自动退回 CPU）。
  说明能看见 GPU 的组合是 **torch 2.8.0+rocm6.4 + LD_PRELOAD 系统 hsa 运行时**；
  RVC 的段错误是这套组合下更深层的 DXG 问题，不是版本能轻易绕过的。
