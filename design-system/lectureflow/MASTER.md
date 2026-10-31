# Design System Master File — LectureFlow

> **LOGIC:** Page files under `pages/` override this Master. Skill CLI teal/orange/web fonts are **rejected**; Cirno ice-blue + macOS HIG win.

**Project:** LectureFlow  
**Category:** Quiet macOS study tool (not dashboard, not kids education clay)  
**Dials:** Variance 2 · Motion 2 · Density 4  
**Stack:** SwiftUI + SF Symbols + system fonts only

---

## Global Rules

### Color (Cirno ice-blue)

| Role | Light | Dark | Notes |
|------|-------|------|-------|
| Accent | `#3A8FBF` | `#6BB3D9` | Sparse ice accent |
| Accent soft | 12% fill | 18% fill | Selection / drop / icon wells |
| Canvas | `#F5F8FB` | `#1C2128` | Cool reading surface |
| Sidebar | `#EEF3F8` | `#161A20` | Soft ice mist |
| Foreground | system label | system label | Prefer semantic text |
| Secondary | system secondary | system secondary | |
| Hairline | label @ 7% | label @ 12% | No teal borders |
| Record | system red | system red | Capture / destructive only |
| Caution | system orange | system orange | Pause / attention |

**Do not use:** teal wash, orange CTA, neon, glass over transcripts, full-page gradients, emoji icons.

### Typography

- **All UI:** SF Pro / `.system` — no Google Fonts, no Comic/Baloo
- Title 22–24 · Section 13 semibold secondary · Body 14 · Caption 11–12
- Transcript EN 15 · ZH 17 (Chinese primary reading weight)
- Timers: monospacedDigit

### Spacing / Radius

4 / 8 / 12 / 16 / 24 / 32 · content inset 28 · sidebar ideal 252  
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
| Empty | Cirno app icon + short copy + one CTA |
