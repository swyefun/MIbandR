# MIbandR

小米手环 11（固件 4.100.179）Z 轴线性马达（LRA）：**实测其谐振频率 f0，并探索用它当作换能器、靠空心容器共鸣发声（在没有扬声器的设备上听到声音）** 的调研与工程记录。

> 状态：调研 + 工具链已完成，真机实测待进行（需要 CI 编译出包 → 装到手环 → 录音 FFT 读谱）。
> 相关项目：[Canopus](https://github.com/AstralSightStudios/Canopus)（原生模块运行时）、[BlueToothMI](https://github.com/swyefun/BlueToothMI)。

## 核心问题

手环 11 的马达是 Z 轴线性马达（LRA）。LRA 是**窄带高 Q 器件**，驱动频率由固件锁定——从 Canopus 逆向出的 `vibrator_run` 波形段只有 `duration_ms` + `amplitude`，**没有频率字段**，所以无法从模块里"扫频"。要测 f0 只能：让马达持续振动 → 用空心容器耦合放大 → 手机麦克风录音 + FFT 读峰值。

此前只有两种互相冲突的推断：
- 通用 LRA 常见 f0 ≈ **175 Hz**（半功率带宽仅约 6.5 Hz）；
- 尺寸高度吻合的安和精密 FZ-HD00/FZ-HB01（10.3×4.25×3.2/3.5 mm）标称 **250±10 Hz**。

本仓库的 `canopus-module/vib-probe` 就是为终结这个分歧而做的实测工具。

## 仓库结构

```
MIbandR/
├── README.md                 本文件：目标、结论索引、实测流程
├── docs/
│   ├── 01-audio-to-vibration.md   音频转震动/触觉的通用技术资料
│   ├── 02-lra-as-speaker.md       LRA 当换能器发声：窄带限制与破解
│   ├── 03-band11-motor.md         手环 11 马达参数（拆解 + 固件接口 + 供应链）
│   └── 04-resonant-cavity.md      空心容器共鸣：亥姆霍兹共振与实测方法
└── canopus-module/
    ├── Canopus.toml           Canopus 原生模块清单（target: 4.100.179）
    ├── src/vib_probe.c        持续激励马达的模块源码
    └── README.md              模块构建/安装/实测步骤
```

## 快速结论

1. **LRA 本质上就是"去掉纸盆的扬声器"**：音圈+永磁体+弹簧悬挂质量块，结构同源。区别是它把振动打进接触的介质（容器/面板），由介质辐射声音。
2. **窄带高 Q 是核心矛盾**：直接灌全频音频会得到严重染色的声音；但反过来，对提示音/单音/节奏，能量集中在 f0 反而响度、穿透力强。
3. **放空心容器里效果特别好 = 双重共振匹配**：LRA 机械共振峰 f0 与容器亥姆霍兹共振 f_H 对上，两级增益相乘，同时辐射面积被容器壁/开口放大。500 ml 瓶（颈 Ø2 cm × 5 cm）≈194 Hz、1 L 罐（颈 Ø2 cm × 3 cm）≈177 Hz——若实测 f0≈175 Hz 则正好匹配；若 f0≈250 Hz 则开口要缩到约 2.8 mm。
4. **实测必须靠外部 FFT**：模块只负责持续激励，录音看谱才能分辨 175 vs 250 Hz。

## 实测流程（摘要）

1. CI 编译 `vib_probe` 出 ELF + receipt → 安装器生成表盘 → Mi8WfBinTool 打包 → 装到手环。
2. 确认手环未关振动 → 激活模块（持续振 5 s、每 4 s 重触发）。
3. 手环放进空心硬壁容器 → 手机 FFT App 录音 → 读峰值频率。
4. `query` 状态字中 `last_rc == 0` 是振动服务确实生效的第一手判据。
5. 换多个不同容积容器交叉验证：多次都出现的峰才是马达真实 f0。

完整步骤见 [`canopus-module/README.md`](canopus-module/README.md) 与 [`docs/04-resonant-cavity.md`](docs/04-resonant-cavity.md)。
