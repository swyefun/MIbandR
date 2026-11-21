# 01 — 音频转震动 / 触觉的通用技术资料

最初的方向调研：把音频转成振动。虽然最终目标是"靠振动发声"而非皮肤触觉，但处理链路与换能器选型高度重叠，保留备查。

## 核心思路

把音频信号的属性映射为振动——音高→振动频率，响度→强度，节奏/瞬态→脉冲时序。不是把声波"原样搬到介质上"，而是按执行器的能力重新编码。

## 标准处理链路

1. 获取音频流（文件、麦克风、系统播放音频副本）
2. **滤波**：低通/带通，只留执行器能有效再现的频段
3. **整流 + 包络跟踪**：把正负交替波形变成非负强度值，起振/释放时间决定"轻拍"还是"渐强"
4. **增益、限幅、阈值**：映射强度、防过载、抑制静音时持续震动
5. **分频/分通道映射**：低频驱动双手/主通道，其他频段驱动不同位置
6. 送给触觉驱动芯片 → 执行器机械振动

## 关键数字

- 皮肤最敏感频段约 **200–250 Hz**（Pacinian 小体），可感知约 20–400 Hz（部分研究 100–1000 Hz）。
- 触觉与声音同步容差约 **提前 60 ms / 滞后 80 ms**，超出即感觉脱节 → 本地处理优于云端。

## 学术发现（哪些信息传得过去）

| 维度 | 触觉可传递性 | 依据 |
|---|---|---|
| 音高（旋律轮廓） | 部分可行，需压缩/移调 | [Translating Music to Touch](https://www.biorxiv.org/content/10.1101/2025.09.30.679532v2.full-text)（IEEE TOH）：触觉频率—感知斜率 0.63 vs 听觉 0.83；锯齿波瞬态清晰、位移效率高，但更粗糙不悦 |
| 起音时间、调幅深度 | **最好**，接近听觉阈值 | [Scientific Reports 2025](https://www.nature.com/articles/s41598-025-21908-4) |
| 频谱类音色 | 能辨别但阈值差 3–5 倍 | 同上 |
| 节奏 | 较可行 | [电子科技大学学报综述](https://www.juestc.uestc.edu.cn/article/doi/10.12178/1001-0548.2021369) |

机制结论：触觉依赖**快速时间包络**（envelope periodicity）而非时间精细结构；存在触觉版"缺失基频"现象（高频载波+低频包络诱发低频音高）。

## 现成平台与芯片

- **Android `HapticGenerator`**（API 31+）：系统级音频后处理，挂到 `AudioTrack`/`MediaPlayer` 的 audio session 实时生成触觉。谐振频率默认 150 Hz（自动链接 `IVibrator.getResonantFrequency`）、慢包络归一化指数 -0.8。需设备支持 audio-coupled haptics。[API](https://developer.android.com/reference/kotlin/android/media/audiofx/HapticGenerator) · [AOSP 调优](https://source.android.com/docs/core/interaction/haptics/haptics-ux-foundation)
- **Apple**：iOS 13+ `Core Haptics` 编排音+触；iOS 18+ **Music Haptics**（iPhone 12 及以后，SE3 除外）。
- **TI DRV2605/L**：专用触觉驱动，内置音频转触觉模式，带 Smart-Loop 自动谐振跟踪、过驱/主动制动。[应用笔记 zhcafj5](https://www.ti.com.cn/cn/lit/an/zhcafj5/zhcafj5.pdf)
- **执行器对比**：ERM 便宜但频率/强度耦合、响应慢；LRA 启停快、适合同步；音圈（voice coil）能近似"播放"波形（DualSense）；压电响应最快、适合瞬态。

## 开源项目

| 项目 | 亮点 | 地址 |
|---|---|---|
| Vibrosonics | ESP32 + MAX9744 + TT25-8；FFT 分离打击乐/旋律后重合成到 0–230 Hz 触觉域 | [GitHub](https://github.com/udellc/Vibrosonics) |
| Sensory Substitution Kit | MAX9814 → ESP32 FFT → 4 频段 → 4 个振动马达 | [GitHub](https://github.com/Bardmalek/sensory-substitution-kit/) |
| RhyFeel | Web Audio API 每 10 ms FFT → ESP32 四路 ERM 手环 | 项目页 |
| Haptify-android | 抓取系统播放音频实时转振动（安卓版 Music Haptics） | GitHub |

## 商业产品

- **SUBPAC M2**：可穿戴体感音频背心，频响 5–130 Hz，换能器把低频传到躯干。
- **PS5 DualSense**：双音圈执行器可直接送波形。
