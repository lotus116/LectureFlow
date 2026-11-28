# Design System Master File — LectureFlow

> **LOGIC:** Page files under `pages/` override this Master. Skill CLI teal/orange/web fonts are **rejected**; Cirno ice-blue + macOS HIG win.

**Project:** LectureFlow  
**Category:** Quiet macOS study tool (not dashboard, not kids education clay)  
**Dials:** Variance 2 · Motion 2 · Density 4  
**Stack:** SwiftUI + SF Symbols + system fonts only

---

## Lecture Rail composition (2026-11-28)

Latest implementation: [scope and per-screen record](../../docs/design/LECTURE-RAIL-2026-11-28.md).
Use a 44 pt tool rail plus the existing collapsible course-name column (168/192 pt), original rounded recorder card,
and full reading surface with 16 pt inner margins. The final user choice restores the complete ba47f7c
rounded recorder card (normal, compact reading, ended summary), with 16 pt horizontal / 8 pt vertical outer spacing.
The intermediate flat recorder band at 86f962a is superseded. State notices grow naturally. Settings groups use a sidebar at >=700 pt, native
segments below. A/B comparison uses two columns at >=700 pt and stacks both texts below.
Ice/Amber tokens, recording semantics, reading identity and floating backing remain unchanged.
Final typography pass: page titles 23 pt, sheets 20, section headings 13 semibold, body/action labels 14,
and auxiliary/rail labels 12. Add 2 pt leading only to selected multi-line helper text outside the recorder.
Use native prominent/bordered hierarchy for course/review actions, existing palette roles, and native focus/disabled.
No global opacity or web cursor substitutions; no subtitle type-token changes.
Final annotated user corrections: use the bundled product brand image in the rail; hide only the two circular
transport captions while preserving their hit areas/help/accessibility; render attention notices at 12 pt regular
with a simple warning icon (failure unchanged); center the active unsaved-audio state inside the remaining list region.
Earlier spacing/card notes below describe the prior baseline where they conflict with this composition.

## Global Rules

### Color (Cirno ice-blue)

| Role | Light | Dark | Notes |
|------|-------|------|-------|
| Accent fill | `#2563EB` | `#3671DB` | Existing native button palette |
| Accent ink | `#1D4ED8` | `#B5D6FF` | Text and icons, separate from fill |
| Accent soft | 12% fill | 18% fill | Selection / drop / icon wells |
| Canvas | `#F8FBFF` | `#101827` | Cool reading surface |
| Sidebar | `#EEF5FC` | `#0C1422` | Soft ice mist |
| Panel | `#FFFFFF` | `#172438` | Reading surface |
| Translation | `#43546A` | `#C3D0E0` | Reading content, not secondary metadata |
| Foreground | system label | system label | Prefer semantic text |
| Secondary | system secondary | system secondary | |
| Hairline | label @ 7% | label @ 7% | Existing native separators |
| Record | `#E64D31` | `#FF8B69` | Existing capture signal |
| Caution | system orange | system orange | Pause / attention |

**Do not use:** teal wash, neon, glass over transcripts, full-page gradients, emoji icons. Ice retains its blue CTA; the authorized Amber alternative uses its own warm accent.

### Typography

- **All UI:** SF Pro / `.system` — no Google Fonts, no Comic/Baloo
- Title 22–24 · Section 13 semibold secondary · Body 14 · Caption 11–12
- Transcript EN 15 · ZH 17 (Chinese primary reading weight)
- Timers: monospacedDigit

### Spacing / Radius

4 / 8 / 12 / 16 / 24 / 32 · content inset 24 (compact 14) · sidebar 220 (compact 168)
Radius 8 / 12 / 16 (continuous) — softer than prior 6/10/14 harsh boxes

### Effects

- Panels: soft ice fill, **no hard stroke** in light; hairline only if needed in dark
- Motion: 160ms easeInOut; respect Reduce Motion
- One primary CTA per region; secondary borderedless or quiet bordered

### Anti-patterns (product brief)

- Per-row cards, dashboard stats, Cirno in transcript body
- Web landing hero, pill clusters, multi-hue button rows
- Claymorphism / kids playful fonts from education product matches

---

## Page intent

| Surface | Intent |
|---------|--------|
| Course home | Course name hero + one primary “开始新 Lecture” + quiet Lecture list |
| Lecture detail | Session strip (timer + record) above content; transcript is the product |
| Sidebar | Brand mark + course/lecture tree; ice selection |
| Empty | App icon on first open; quiet contextual symbol within a course; one primary CTA |

## Native reading checks · 2026-11-28

This table now matches `LFChrome` and the previously confirmed native palette;
the older teal-like values were stale documentation, not a new theme direction.
Only the translation foreground is new in this iteration.

- Start a Lecture: keep the recording mode visible beside the primary action;
  expose existing empty-Lecture and import actions as secondary buttons.
- Session notices wrap independently of the mode/model controls. Keep pause and
  end labels visible in the regular recorder, retaining the compact reading strip.
- Main transcript: Chinese is body content; use the translation token for live
  and offline text. Copy/export share a secondary menu; bookmark and latest stay visible.
- Floating paragraphs wrap without a line cap. Keep scrolling available at the
  smallest width and 56-point size; manual reading pauses following, latest resumes it.
- Font buttons expose independent accessible names. Do not animate streaming caption updates; respect Reduce Motion for
  user-triggered expansion and sidebar changes. Existing bookmark focus/reveal behavior stays intact.

Evidence and remaining limits: [native frontend review](../../docs/design/FRONTEND-2026-11-28.md).

## Two light palettes · 2026-11-28

Ice remains the Cirno-inspired default with its exact existing colors. The optional light palette is
Opus-directed **Amber / 琥珀**, replacing warm-stone / forest while retaining the stored `forest` identifier.
No migration or system/light/dark selector is added. The existing alternative dark palette and dark
floating backing remain unchanged. Semantic colors live in `Theme.swift`; geometry and typography in `LFChrome`.

Amber light: canvas `#FAF7F4`, sidebar `#EDE7DF`, panel `#F4EFE8`, selection `#FAECD4`, recorder `#FFF5E8`,
accent `#B45309`, link `#92400E`, body `#1C1410`, secondary/translation `#78614F`, hover `#F5EEE4`.
Use `bodyText`/`secondaryText` for the alternative light palette; keep Ice and existing dark system labels.
Do not reuse the rejected low-contrast metadata proposal `#9C7B65`.

`LFStatusBanner`: 13-point text; adaptive horizontal/stacked actions; 10×8 inset; no fixed height.
Information/working use a quiet neutral surface, attention/error add a semantic marker and symbol.
`LFReviewChange`: 13-point monospaced text, wrapping +/- content, explicit accessible added/removed labels.
Diff semantic inks are independent of theme accents; recording/end remain red.
Primary content tabs (audio/transcript) are distinct from subordinate transcript source choices.
Sheet titles use 20-point semibold; native grouped forms, menus and confirmation panels remain native.

Dark floating subtitles retain adjustable translucent edges with a local contrast floor behind text
and controls. Streaming updates never animate scrolling. No new motion was added this round.

Use the [surface inventory](../../docs/design/UI-SURFACES-2026-11-28.md),
[theme specification](../../docs/design/THEMES.md), and
[current implementation and evidence](../../docs/design/OPUS-FRONTEND-2026-11-28.md).


### 独立内容区空态

使用 LFCenteredContent 的有界父容器双轴居中；工具栏、标题、错误和非空列表不居中。
低高度横排图标与说明，文字自然折行，极小高度滚动；不使用物理屏幕中心、固定偏移或无界 ScrollView 高度。
欢迎、课程无讲次、录音、转写零匹配、资料、任务、废纸篓和复核共用规则。


### 真实窗口跟进：课程与稳定录音卡

常规课程栏220点，窄窗200点；课名／讲次两行真实行高加留白，不缩字号；尾部省略保留完整tooltip与AX。
课程箭头单独可聚焦并复用原展开状态。工具轨图标单独显示，保留40×42点击区、品牌和完整名称。
录音圆角卡内固定主控、模式与标记工具行；长通知、保存、标记反馈／重试在卡下状态区，不撑高卡片。
只隐藏状态吻合的原常规暂停确认，未知通知和错误保留。详见[跟进依据](../../docs/design/LECTURE-RAIL-FOLLOWUP-2026-11-28.md)。
