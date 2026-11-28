# Lecture Rail：波形核查、浮窗入口与可调课程栏

2026-11-28 用户在实际窗口反馈后授权，仅在原隔离前端分支从 `67cabf3bf0fdf9f64e5e1cc978095507f3004280`
继续实现。不推送、不安装、不覆盖旧预览或主资料库。正式版本仍为0.6.5；本文件记录本地候选，非发布说明。

## 诊断与实施

- 比较 ba47f7c、71660e2、67cabf3：RecordingWaveformStrip 完整实现逐字一致，LiveController 的 onLevel
  发布、level 观察和0.09秒滚动计时器均在。此前隔离预览每次提交4000个零，结束截图也不应运动。
  本轮不改生产波形或采集逻辑；预览菜单新增“输入：变化音量（合成）”与“输入：静音”。生成440Hz PCM
  仍经过原 CapturePump RMS、onLevel、LiveController.level 和原View；无随机波形，不打开麦克风、不加载模型。
- 全局工具轨以原悬浮字幕 toggle 替换重复后台任务；课程/讲次页共用此轨，移除讲次标题的重复入口。
  原菜单“仅显示字幕窗口”保留；右上原后台任务入口保留。浮窗只表示实际开/关，不增加语言模式或首次提示。
  图形填充、AX名称/值随显示状态变化；无讲次时只是显示原空浮窗，不开始采集。
- 课程栏边界改为6点命中范围的原生分隔线，细线仍保持轻量；鼠标拖动、聚焦后左右键每次10点、AX增减与设置值。
  最小180、最大360，并为主内容保留至少480点；窗口缩小时只夹住实际宽度，不覆盖用户宽度偏好。
  新UI偏好 librarySidebarWidth，零/不存在沿用原200窄窗、220正常窗；原显示偏好不变，隐藏恢复和重启保留宽度。
  无旧宽度偏好需要迁移。不引入740强制隐藏、叠层或新路由。拖动不加布局动画。
- 课程标题的折叠箭头、文件夹按第一行16点行高对齐；保留两行课名、40点按钮和原选择/折叠/拖放行为。
- 品牌未改。原PNG与已安装ICNS四角alpha均255，无透明外边距；打包脚本直接缩放原图生成ICNS，
  预览另外把原PNG设为运行时图标。轨道上的圆角裁剪不作用于Dock；不能假定macOS给旧ICNS自动套圆角。
  新图标候选由主会话另行处理，本轮不替换任何品牌资产。

## 设计指导取舍

本轮复用主会话提供的真实Opus文字意见，实施者未另外调用外部模型。采纳真实幅度、暂停冻结、可访问拖动、
首行对齐和全局浮窗入口。拒绝强制窄窗叠层、双语/单语/关闭三态图标、一次性提示及未经选择的品牌简化。
不加重复录音点：原红播放头和“正在录音”已表达状态。静音不强制一帧抹掉历史：滚动波形保留已有幅度，
历史滚完后平直；这是历史显示，不是虚构当前声音。

## 验收方式与边界

新独立预览 interaction-before 从明确67cabf3导出产品源码，interaction-after 使用候选；两者各用独立bundle、
UserDefaults和合成库。修正预览构建工具的 source-ref 导出命令，确保用传入的提交而不是当前HEAD。
运行窗口内容渲染，不捕获锁定/状态未知的桌面，不上传内容。

- 变化音量0.006/0.06/0.16经实际RMS得到约0.03394/0.33941/0.90510；同窗口三帧局部波形不同。
- 暂停两帧相同，继续后两帧不同；静音level=0且历史清空后两帧相同；结束保存一段生成录音。
  首次3.5秒静音检查尚有一条历史柱未滚出；保留失败日志，5.5秒后验证稳定，不改生产历史长度。
- 740/820/1080宽窗长中英课程/讲次、拖动限制、键盘与AX调整、隐藏恢复、重启偏好、折叠选择；
  实际事件命中原生分隔线，并读取最终布局与宽度，不直接用偏好赋值冒充拖动。
- 浮窗轨入口在课程与讲次都检查，无讲次不启动采集；任务从右上原按钮打开，Escape关闭。
  测试首次直接close系统sheet未完整结束模态会话，保留失败；后续使用实际Escape入口。

必要回归：原生59通过、1真实后端课件集成测试跳过；合成suite、空态、课堂流程（观察副本减少动态效果）、
键盘复核回听后Escape停播、主题选择/恢复默认和重启偏好均通过。未改生产动态效果默认。

最终结果、精确SHA、命令、图像和日志清单见本地 `reports/private/opus-redesign/interaction-report.md`。
真实麦克风、前台系统焦点/完整VoiceOver、课堂质量仍未测；窗口内容渲染不等于桌面实拍。
正常前台“返回最新”动效限制仍沿用[上一轮说明](LECTURE-RAIL-FOLLOWUP-2026-11-28.md#后台动画验收限制)。

## 可复现入口

```sh
python3 scripts/preview-frontend.py --name interaction-before --source-ref 67cabf3 --audit --no-activate --python /absolute/path/to/existing/.venv/bin/python
python3 scripts/preview-frontend.py --name interaction-after --audit --no-activate --python /absolute/path/to/existing/.venv/bin/python
python3 scripts/verify-frontend.py --name interaction-after --probe=--interaction-audit --no-capture
python3 scripts/verify-frontend.py --name interaction-after --probe=--verify-sidebar-persistence --no-capture
```

手动打开新隔离预览后，在“合成预览”菜单选变化音量，再点击开始/继续；音量档位每秒切换。
暂停时冻结，继续后变化；选择静音并等待历史滚出后平直。仅作用于预览，不是产品录音源设置。
