# vib-probe — 手环 11 马达 f0 实测模块

一个 Canopus 原生模块：让小米手环 11（固件 4.100.179）的 Z 轴线性马达
**持续振动**，配合空心容器耦合 + 手机麦克风 FFT，测出马达的真实谐振频率 f0，
用来判定它到底是 ~175 Hz 还是 ~250 Hz（此前只有尺寸推断，未实测）。

## 原理

LRA 是窄带器件，驱动频率由固件锁定（`vibrator_run` 的波形段只有 duration /
amplitude，没有频率字段），模块无法扫频。但给窄带 LRA 任何持续激励，它都只在
自身 f0 上高效振动。所以：模块负责"持续激励"，你负责"录音 + 看频谱峰值"。

空心容器（瓶/罐）本身有亥姆霍兹共振，会把手环传来的结构振动高效转成空气声。
把 f0 与容器共振对上，声音最响——频谱峰值就是两者耦合后的共振点。

## 行为

- `activate`：立即触发一次 5000 ms 长振动，并用 4000 ms 周期定时器不断重触发，
  使马达近似持续振动，留足时间录音。
- `deactivate` / `stop`：停定时器并取消振动。
- `query`：输出 6 个 u32 状态字（magic/version/active/ticks/last_rc/timer_id），
  用于确认服务确实跑起来了：`last_rc == 0` 表示振动服务调用成功。

## 构建与打包（CI，无本地交叉工具链）

已接入 `scripts/ci.sh` 的 gate 5，push 后 CI 会编译 + 打包：

```
cd modules/examples/vib-probe && canopus build && canopus package
```

## 安装到手环

1. CI 产出 `vib_probe` 的 ELF + receipt 后，用安装器生成表盘：

   ```
   python scripts/build_module_installer_prod.py --product vib-probe --all
   ```
   （`PRODUCTS` / `PRODUCT_TARGETS` 已登记，只针对 `xiaomi-band-11-4.100.179`。）

2. 把生成的 device 目录下的 `main.lua` 和 `.bin` 打包成表盘 `.face/.bin`
   （用 Mi8WfBinTool），作为安装器表盘装到手环。

3. 手环设置里确认「振动」未关闭（系统关闭时 haptics 返回 ERR_STATE，模块
   `last_rc` 会是负值）。

4. 在手环上激活 vib_probe 模块 → 马达开始持续振动。

## 实测 f0

1. 手环（马达面）贴/放进空心容器（推荐 500 ml 瓶或 1 L 罐，硬壁、刚性）。
2. 手机打开频谱/分贝 App（能显示 FFT 频谱的，如 "Spectroid" / "Audio Spectrum"
   之类），麦克风靠近容器口。
3. 读频谱最突出的峰值频率：
   - 峰值 ≈ 175–200 Hz → 马达 f0 偏低，接近通用 LRA，容器 500 ml/1 L 正好匹配；
   - 峰值 ≈ 250 Hz → 吻合安和 FZ-HD00/FZ-HB01 的 250±10 Hz 规格，
     那么腔体开口要更小（1 L 罐颈 Ø≈2.8 mm）才能匹配。
4. 注意：测到的是「马达 f0 与容器共振耦合后」的峰。要分离两者，可换几个
   不同容积/开口的容器，取多次都稳定出现的那个峰作为马达 f0。

## 已知约束

- 同一时刻只有一次振动；新脉冲会替换旧脉冲，重触发瞬间有极短断振，
  取 FFT 稳定段的峰值即可，不影响 f0 判定。
- `vibrate` 单次上限 5000 ms，所以用 4 s 周期重触发来覆盖。
- 这是静态恢复 + 模拟器证据，未在真机验证；模块 `query` 的 `last_rc` 是
  判断真机是否真的驱动成功的第一手依据。
