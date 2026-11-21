# 独立原生应用：vibe-player（手环 11 / 4.100.179）

## 目标

不装 Canopus Supervisor 框架，做一个只做一件事的最小原生应用：
**通过固件 `vibrator_run` 以真实逐段幅度（amplitude 0-255）驱动 LRA**——
这是"马达放音乐"（幅度调制）的能力基础，Canopus 官方 haptics API 没有暴露它
（平台层写死 amplitude=255）。

## 架构

```
表盘（本仓库 native/watchface）
  ├─ recover_execute.lua   恢复固件 os.execute（Lua debug 接口）
  ├─ native_loader.lua     NSH mw/exec + Umem 字符串定位 → 启动 stage1
  ├─ stage1 → stage2       shellcode + ELF32 loader（重定位 + .init_array）
  └─ vibe_player.elf       我们的 payload（零导入 ET_REL）
        └─ constructor: register_driver("/canopus/vibe", fops)
表盘按钮 → io.open("/canopus/vibe","wb") 写 1 字节命令
payload → 函数指针直调 0x0C6D3D31 vibrator_run(20 字节参数块, kind=2)
          6 字节波形段 {offset u16, duration u16, flags u8, amplitude u8}
```

## 命令字节

| 字节 | 行为 |
|---|---|
| 0x00 | 停止（vibrator_cancel） |
| 0x01 | 扫幅度：16/64/128/200/255，各 900ms 振 + 800ms 停 |
| 0x02 | 短脉冲：50/100/200/400 ms @255 |
| 0x03 | 恒振 3s @255（对照） |

**0x01 是关键实验**：验证波形段 amplitude 是否真的改变响度——
这是所有后续"音频转幅度序列"工作的地基。放空心容器里录音，
5 个峰的响度应单调递增。

## 构建

- `native/scripts/build_payload.sh`：clang（arm-none-eabi Cortex-M33）
  → `ld.lld -r -T loader/sections.ld` → objcopy strip（保留重定位/构造器）。
  CI 安装 LLVM 后执行。
- `native/scripts/build_installer.py`：以 `baseline/` 里**真机验证过的**
  loader profile 为基线，只替换 supervisor_size/CRC 为 payload 的值
  （stage1/stage2 二进制原样复用，CRC 自动校验）；把 recovery 字段
  （lua-recovery.toml）注入 main.lua 的 PROFILES；组装 dist/。
- payload 资源名保留 `canopus_supervisor-<target>.bin`（loader 写入
  /data/canopus/supervisor.elf 的文件名未改，离已验证配置最近）。

## 来源与合规（AGPL-3.0）

loader 链（recover_execute / native_loader / stage1/2 / ELF loader /
progress.lua / baseline profile）来自 Canopus（AGPL-3.0），
完整许可证文本见 `native/licenses/CANOPUS-AGPLv3-LICENSE`。
因此本仓库整体以 AGPL-3.0 发布，源码公开。

## 分阶段计划

1. ✅ 本轮：payload + 表盘 + 构建链 + CI（CI 出 vibe-player-watchface/）
2. 真机：装表盘 → 加载引擎 → 扫幅度录音 → 确认 amplitude 语义
3. 加 L2 蓝牙接收（channel 0x43 钩子，参考 canopus_supervisor_l2.c）
   + 手机端把音频转成 (duration, amplitude) 帧流下发
4. （可选）boot 固化：/data/canopus/boot.lua 开机自启引擎

## 已验证 / 待验证

- ✅ vibrator_run 在 .179 真机可用（vib_probe 持续振出 256 Hz 实测）
- ✅ bootstrap 链（stage1/2、profile 地址、指纹、MPU 检查）真机跑通
- ✅ supervisor 构建产物 CRC/尺寸与 profile 绑定逻辑（CI 复用同一机制）
- ⚠️ payload 里 amplitude 的语义（0x01 扫幅度实验，待真机）
- ⚠️ /canopus/vibe 路径可被表盘 Lua 打开（/dev /proc /sys 被拒，
  /canopus/* 已由 Canopus 的 /canopus/install 证明可行）
- ⚠️ wave 一次调用多段数上限（本设计最多 10 段，保守）
