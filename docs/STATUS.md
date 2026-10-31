# 当前状态 · 2026-10-31

## 简化版产品 + 原生 UI 视觉改进（0.6.0）

产品行为以 [PRODUCT_DESIGN-2026-10-31](PRODUCT_DESIGN-2026-10-31.md) 为准；界面按 [UI_REDESIGN_BRIEF.md](UI_REDESIGN_BRIEF.md) 做了安静、冰蓝点缀的 macOS 工作区整理。功能追踪见 [SD_TRACKING.md](SD_TRACKING.md)。

### UI 视觉（本轮）

| 项 | 状态 |
|---|---|
| 代码：Chrome token、侧栏、课程主页、采集条、录音列表、转写正文（英上中下）、资料/任务文案收敛 | 已改 |
| 改前/改后截图（合成数据 · ~1180 光主题） | [assets/ui-redesign](assets/ui-redesign/README.md) 已检查 |
| 暗色主题、920×610、1440×900、悬浮窗实机、键盘焦点全覆盖 | 待本机再核 |
| 假波形/假进度/硬编码正文 | 未引入 |

### 功能验证证据

- ruff 通过；pytest 93；原生测试 19；`scripts/build-app` 产出 `dist/dev/LectureFlow.app`
- 质量、延迟、续航、三小时：**暂缓，未通过**

### 未解决 / 待测

- 暗色与多窗口尺寸系统对照截图
- 真实麦克风、睡眠、首次权限
- 提高对比度 / 减少动态效果偏好下的细部
- 不自动正式发版

## 历史

0.5.x 证据仍有效，不等于简化版或本轮 UI 已全部视觉验收。
