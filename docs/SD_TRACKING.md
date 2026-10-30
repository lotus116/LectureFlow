# SD-01–SD-11 实现追踪 · 2026-10-31

基线设计：`PRODUCT_DESIGN-2026-10-31.md`。状态：`todo` / `wip` / `done`（须有证据）。不可因设计确认而打勾。

| 编号 | 现状（0.5.1） | 差异 | 主要位置 | 验收 | 状态 |
|---|---|---|---|---|---|
| SD-01 | 侧栏课程→课堂；Lecture 内五页签 | 课程主页、两内容页、Lecture 命名、取消导入无空记录 | `LectureFlowApp.swift`、`Workspace.swift` | 行为/UI | todo |
| SD-02 | course/lecture 双归属资料 | 统一课程资料；去重/替换/失败隔离/确认删除 | `documents.py`、`MaterialsView.swift` | 行为测试 | todo |
| SD-03 | 术语需采用；有确认门槛 | 自动启用、人工优先、来源清理、相关小词表 | `knowledge.py`、`inference_policy.py` | 行为测试 | todo |
| SD-04 | 三种方式与暂停继续结束 | 追加录制、细分开关收设置、模型未就绪路径 | `SessionControls`、`LiveController` | 行为/UI | todo |
| SD-05 | 实时/转写分栏；总结页 | 统一转写页、历史、编辑保护、显式重译、导出 | 新转写视图、`exporting.py` | 行为/UI | todo |
| SD-06 | 片段列表暴露内部块感 | 用户段与底层块分离；变化提示；任务快照 | `recordings.py`、`library.py` | 行为测试 | todo |
| SD-07 | 悬浮窗较完整 | 精简控件、关闭/退出区别、采集中关主窗提示 | `SubtitleWindow.swift` | UI/行为 | todo |
| SD-08 | 有任务队列与让路 | 等待原因、手动暂停不自启、取消终态明确 | `worker.py`、`model_lease.py` | 行为测试 | todo |
| SD-09 | 移动/废纸篓存在 | 冲突保护文案、跨 Lecture 录音移动入口移除 | `files.py`、`purging.py`、UI | 行为测试 | todo |
| SD-10 | 多资料库设置、总结密钥 | 单资料库、四类设置、诊断脱敏 | `SettingsView.swift`、`RuntimePaths` | UI/行为 | todo |
| SD-11 | 笔记/API/Prompt 仍在 | 移除界面、命令、导出产物与 Keychain 总结依赖 | `summaries.py`、`SummaryView`、CLI | 测试更新 | todo |

阶段进度：A（SD-01–03、11）→ B（04–06）→ C（07–10）→ D 验收。
