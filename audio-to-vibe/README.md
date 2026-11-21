# audio-to-vibe — 手机端音频转振动（最小测试）

把音频在手机/PC 上转换成振动序列，经 CanopusConnect 发给手环 11，由马达播放。

## 核心约束（决定了方案）

1. **LRA 是窄带器件**：实测 f0 ≈ 256 Hz，驱动频率固件锁定，无法表现音高/波形，
   只有**节奏 + 响度包络**能保留下来。
2. **模块 haptics API 无幅度控制**：`vibrate_pattern(uint16_t pattern[], count)`
   只接受 ON/gap 时长交替，最多 16 项、10 ms 精度、单次 ≤ 5000 ms。
   响度只能编码成 **ON 脉冲时长**（越长振得越久 = 主观越强）。
3. **传输通道**：CanopusConnect（ADR-CAN-009），手机 → 模块字节流，单帧 ≤ 480 B，
   已实现、未在真机验证。模块侧用 `ctx->connect->listen()` 收字节。

所以转换链路定为：

```
WAV → RMS 包络 → 阈值门控 → 游程编码 ON/gap
    → 10 ms 量化 → 拆成 ≤16 项 pattern → uint16_t[] → （下一步）CanopusConnect 发送
```

## 最小测试（已跑通）

```bash
python3 make_test_audio.py out/test_rhythm.wav   # 造 1.4s 测试音（6 段节奏）
python3 audio_to_vibration.py out/test_rhythm.wav -o out
```

测试音的 6 个音段（强/弱/强/弱/长音/尾点击）被正确还原成 6 个 ON 脉冲：

```
ON 脉冲 (ms): [150, 100, 150, 100, 300, 100]
gap   (ms): [ 50, 100,  50, 100,  50, 150]
pattern: {150, 50, 100, 100, 150, 50, 100, 100, 300, 50, 100, 150}  (12 项)
```

验证项全部通过：项数 ≤ 16、每项 ≤ 5000 ms、10 ms 对齐、段数与音段一一对应、
长音（300 ms）体现为更长脉冲。

## 产物（`out/`）

- `test_rhythm.wav` — 测试音
- `vibration_pattern.h` — 可直接粘进 Canopus 模块的 `uint16_t[]`
- `vibration_pattern.json` — 机器可读 pattern + 元数据
- `preview.txt` — 包络 ASCII 图 + pattern 时间线

## 用法

```bash
python3 audio_to_vibration.py <input.wav> -o out/
python3 audio_to_vibration.py <input.wav> --threshold 0.12 --frame-ms 20 --min-gap-ms 30
```

- `--frame-ms`：RMS 分析帧长（默认 10 ms，等于 API 精度）
- `--threshold`：绝对 RMS 门限；不给则用 `--relative`（默认峰值 RMS 的 15%）
- `--min-gap-ms`：短于此的静音并入前一个脉冲，避免节奏被抖断

纯 Python 标准库（`wave`/`array`/`math`），PC 和手机 Termux 都能跑。
输入限 16-bit PCM WAV（单声道直读，立体声取平均）；MP3 先用 ffmpeg 转 WAV。

## 下一步（未做）

1. 手环侧模块：`ctx->connect->listen()` 收 pattern 字节流 → `vibrate_pattern()` 播放。
2. 手机侧：AstroBox 插件按 ADR-CAN-009 用 `canopusconnect.send(binary)` 分帧发送。
3. 真机验证：振动是否可听/可感、节奏是否跟随、延迟是否可接受。
4. 如需幅度维度：要扩展 Supervisor（固件 `vibrator_run` 原生支持逐段 amplitude，
   但当前 C API 未暴露，符号 restricted/PENDING）。
