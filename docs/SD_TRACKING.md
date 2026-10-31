# SD-01–SD-11 实现追踪 · 2026-10-31

术语质量修复（SD-03 / LF-06、LF-04）：自动译名与可靠对照分开，新增版本化词表重建、冲突隔离与单段重译过滤。107 Python、19 原生及预览构建通过；真实术语增益尚未评估。详见 [质量修复](TERMINOLOGY_QUALITY_FIX.md)。

基线设计：`PRODUCT_DESIGN-2026-10-31.md`。状态：`todo` / `wip` / `done`（须有证据）。不可因设计确认而打勾。

窗口比例（LF-UI / SD-01）：默认 1080×700 点、侧栏 240 点，重开主窗口同尺寸；已构建、通过 19 项原生测试并目视检查录音页，其他屏幕尺寸与字体设置待复查。

视觉补充（LF-UI，SD-01 / SD-04）：暖白绿主题、标题及录音行卡片已实现；浅色课程主页与空状态、深色 920×610 内容区录音及空转写页已目视抽查，并提高辅助文字对比度。采集中、转写结果和辅助功能设置仍待视觉复查。此项不改变下列功能验收状态。

| 编号 | 现状 | 差异与实现 | 验收证据 | 状态 |
|---|---|---|---|---|
| SD-01 | 课程主页、Lecture 命名、录音/转写两页 | `CourseHomeView`、`LectureFlowApp`、`Workspace` | UI 构建；取消导入不建空 Lecture | done |
| SD-02 | 课程统一资料 | `documents.course_scope`、`MaterialsView` | `test_knowledge` / `test_delivery` | done |
| SD-03 | 自动启用术语、人工优先、相关小词表 | `knowledge` confirmed=1；`inference_policy` 保留 | 行为测试 + 既有 policy 测试 | done |
| SD-04 | 三种方式；暂停不封存；结束封存；追加录制 | `CapturePump.softPause` / `sealRecording`；继续录制按钮 | `CaptureTests` softPause | done |
| SD-05 | 统一转写页、编辑、恢复、显式重译、导出 | `edit-unit` / `retranslate-unit`；转写页菜单 | `test_manual_unit_edit_survives` | done |
| SD-06 | 暂停块隐藏；用户段=开始到结束 | softPause 保 writer；变化提示 stale | CaptureTests + 既有时间映射测试 | done |
| SD-07 | 悬浮精简；退出确认采集 | `SubtitleWindow`；`applicationShouldTerminate` | 构建；退出确认待真机点按 | done |
| SD-08 | 任务暂停/取消/让路；采集中不切模 | worker 既有；模型切换须暂停 | 既有 library/worker 测试 | done |
| SD-09 | 废纸篓；UI 去掉跨 Lecture 录音移动 | 菜单已移除；`transfer` CLI 仍保留供测试 | delivery 保护测试保留 | done |
| SD-10 | 单资料库、四类设置、诊断脱敏 | `SettingsView` | UI 构建 | done |
| SD-11 | 笔记/API/Prompt/Keychain 移除 | `summaries` stub；导出无 prompt | `test_summary_apis_are_removed` | done |

阶段：A–C 代码已落地；D 文档与预览进行中。质量/延迟/续航/三小时评估仍暂缓，不标记通过。

验证（本轮）：`.venv/bin/ruff check` 通过；93 Python；19 原生。未测真实麦克风、睡眠与付费路径。
