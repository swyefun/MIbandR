# 03 — 手环 11 马达参数

## 已确认的官方/拆解参数

| 项目 | 参数 |
|---|---|
| 类型 | **Z 轴线性马达（LRA）**，小米官方 FAQ 确认 |
| 实测尺寸 | **10.29 mm × 4.25 mm**（智研所拆解），厚约 3.2–3.7 mm |
| 沿用情况 | 与小米手环 9 / 9 Pro 马达尺寸几乎一致（10.32×4.26×3.68 mm），三代同款 |
| 连接方式 | 金属弹片连主板，FPC 露铜，无引线 |
| 供应商 | **四川安和精密**（小米长期供应商，获小米最佳品质奖；手环马达出货约 0.717 亿台/年，全球份额 ~37%） |

## 高度疑似的型号：安和 FZ-HD00 / FZ-HB01

尺寸 **10.3×4.25×3.2 mm / 10.3×4.25×3.5 mm**，与手环 11 实测尺寸**逐位吻合**：

| 参数 | 值 |
|---|---|
| 谐振频率 f0 | **250 ±10 Hz** |
| 额定电压 | 1.8 Vrms AC |
| 电流 | Max 70 mA |
| 振动量 | Min 1.0 Grms |
| 电阻 | 28 Ω ±10% |
| 启/停时间 | 40 ms / 60 ms |

**实测确认（2026-11-21）**：f0 ≈ **256 Hz**。用 `vib_probe` 模块持续激励 + 空心容器 + 手机 FFT 录音，频谱在 256 Hz 出现 -45 dB 窄带峰，瀑布图显示该峰贯穿整个时间轴（持续稳态），并在 512/768/1024 Hz 等整数倍处见谐波尖峰。256 Hz 落在上表 250±10 Hz 规格范围内，**确认是安和这对型号（或其同规格件），通用 175 Hz 假设排除**。

**检测方法**：见 [`canopus-module/README.md`](../canopus-module/README.md)。幅值最高处受容器共振影响，f0 以稳定窄带峰的位置为准。

## 从 Canopus 项目读出的固件接口（关键）

> 这部分是快应用 API 拿不到的、只靠本地逆向项目得到的信息。

- **底层入口**：`vibrator_run`，手环 11 / 4.100.179 地址 `0x0C6D3D31`，参数块 20 字节，`kind=2` 为波形。
- **决定性发现**：波形段结构只有 `duration_ms`(u16) + `amplitude`(u8) + flags/offset —— **没有频率字段**。
  ⇒ LRA 驱动频率由固件锁定，模块**无法扫频**。实测只能"持续激励 + 录音 FFT"。
- 快应用（RPK）路线作废：Vela `system.vibrator` 只有 `vibrate(long/short)`，`start()` 在手环上明确不支持，更无频率接口。
- `haptics` 服务在 179 可用（走 custom loader + supervisor），`vibrate()` 单次上限 5000 ms，新脉冲替换旧脉冲。

## 实测 f0 的三种免费方法

1. **扫频听最响点**：信号发生器 App 输出正弦波，经小功放驱动马达，20→1000 Hz 慢扫，找峰值。
2. **阻抗法**：测线圈阻抗随频率变化，LRA 在谐振点阻抗出现峰值（28 Ω 基础上跳变）。
3. **容器反推法**：固定容器扫频找最响频率，反推腔体共振，再与马达扫频对比是否重合。
4. **本仓库方案**：Canopus 模块持续激励 + 空心容器耦合 + 手机 FFT 读谱（不需要额外驱动硬件）。

## 模块状态输出

179 是 zero-import 目标——模块里任何未定义外部符号都会被 verifier 拒绝。status-writer 辅助函数（`canopus_status_put_u32` 等）的实现在 Supervisor 的 `runtime/control/canopus_control.c`，模块若直接调用会产生未定义符号。官方 `all-services-test` 模块的 `query` 直接 `(void)writer; return 0;`，`hello` 则把 5 个 runtime 源文件列进自己的 sources 才能用。

`vib_probe` 只需持续驱动马达，不依赖 portable runtime，因此 `query` 采用空实现（同 `all-services-test`）。振动是否生效靠**外部观察**：马达持续振动 + 录音频谱出现稳定峰值。若系统关闭振动，马达不振，需先检查手环振动开关。

## 来源

- [小米官方 FAQ（Z 轴线性马达）](https://www.mi.com/hk/support/faq/details/KA-732433)
- [手环 11 拆解报告](http://mp.weixin.qq.com/s?__biz=MzkzMjI4MTc5Mw==&mid=2247496847&idx=1&sn=76d55c47d1a749f96b4fd25550c3b960&chksm=c3c5455bd40339cbc41e00b8fe82710c734721ba4e4158d1044c0aa9685aa7e1b425da72896a#rd)
- [安和穿戴马达参数表](https://www.awaseimitsu.com/Application/2.html) · [FZ-HB01 详情页](https://www.awaseimitsu.com/product/157.html?productCateId=11)
- [安和：小米供应商确认](https://xinsanban.eastmoney.com/Article/NoticeContent?id=AN202210311579668087&pageIndex=50)
