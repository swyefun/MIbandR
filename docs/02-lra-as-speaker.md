# 02 — LRA 当换能器发声：窄带限制与破解

目标转向"在没有扬声器的设备上听到声音"后，核心方案：用 Z 轴线性马达（LRA）当换能器，把振动打进介质（容器/面板/外壳），由介质辐射空气声。

## LRA = 去掉纸盆的扬声器

结构（音圈 + 永磁体 + 弹簧悬挂质量块）与动圈喇叭几乎一样。区别：喇叭推纸盆推空气；LRA 把振动直接打进接触的介质，由介质当辐射面。厂商（MinebeaMitsumi）资料明确写 LRA "可作为扬声器使用，震动原理类似于音响，可针对平面环境提供立体音效"，并给出 LRA 贴合面板发声方案。

## 核心矛盾：窄带高 Q

- 典型 Z 轴 LRA 谐振点 **f0 ≈ 175–180 Hz**，**Q ≥ 15**，半功率带宽往往仅约 **6.5 Hz**。
- 只有 f0 附近振幅大；偏离几赫兹即急剧衰减。
- 直接灌全频音频 → 低保真、严重染色。专利原文：LRA "only operate as a practical speaker across part of this range"。

## 三个破解方向

1. **EQ 校正 / 压缩**：DSP 把 f0 峰值压下去、带外提上来，使"处理器+LRA"合成频响趋于线性。[US20180139538A1](https://patents.google.com/patent/US20180139538A1/en) 用此法让 LRA 当手机听筒（<10 cm 近距离语音）。
2. **换宽带 LRA**：选低谐振、宽带宽型号，如 Vybronics VLV152564W（f0≈80 Hz，标注 "usable into the audio frequency range"）。
3. **多执行器 + 面板共振**：多个不同 f0 的 LRA 拼出更宽频段；不同位置做立体声/声像移动（专利 US20250008264 / US20260281615）。介质选择也关键：硬而薄的材料（亚克力、玻璃、胶合板、碳纤维、石膏板）响亮清晰；厚重材料（实木、混凝土）抑制振动。

## 驱动电路

- **触觉驱动 IC**（TI DRV2605L）：音频转触觉模式，监测模拟输入自动驱动 LRA，带自动谐振跟踪和闭环制动。
- **音频功放 + 切换开关**：Class D 功放直接驱动 LRA，模拟开关在"触觉模式/音频模式"间切换，共用一个执行器。
- LRA 必须 **AC 驱动**：直流只会把质量块推到一边卡住。

## 现实预期

- **适合**：提示音、铃声、来电、近距离语音、节奏/低频、隐藏式/密封面板发声（玻璃门、桌面、仪表盘、设备外壳）。
- **不适合**：全频高保真音乐——受窄带限制，音质音量远不如真喇叭；音量强依赖介质面积与共振特性。
- **空心容器是特例**：当容器共振与 LRA f0 匹配时，响度大幅提升（详见 `04-resonant-cavity.md`）。

## 关键来源

- [MinebeaMitsumi LRA 介质发声原理](https://www.horustech.com.cn/minebeamitsumi-lra-intro.html)
- [TI：LRA 工作原理（谐振/高 Q）](https://www.ti.com.cn/lit/pdf/ssztap9)
- [US20180139538A1：LRA 当扬声器，EQ 校正窄带](https://patents.google.com/patent/US20180139538A1/en)
- [Vybronics 宽带 LRA](http://vibratormotor.com/products/linear-lra-vibration-motors)
