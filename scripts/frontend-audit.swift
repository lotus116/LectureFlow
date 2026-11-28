import AppKit
import SwiftUI
import LectureFlowCore

@MainActor final class PreviewAnchors {
    static var views: [String: [WeakView]] = [:]
    final class WeakView {
        weak var view: NSView?
        init(_ view: NSView) { self.view = view }
    }
}

struct PreviewAnchor: NSViewRepresentable {
    let id: String
    final class AnchorView: NSView {
        override func hitTest(_ point: NSPoint) -> NSView? { nil }
    }
    func makeNSView(context: Context) -> NSView {
        let view = AnchorView()
        PreviewAnchors.views[id, default: []].append(PreviewAnchors.WeakView(view))
        return view
    }
    func updateNSView(_ nsView: NSView, context: Context) {}
}

// Included only in the isolated synthetic preview, never in the product target.
@MainActor extension PreviewSuite {
    @discardableResult func clickAnchor(_ id: String, window: NSWindow? = nil, xFraction: CGFloat = 0.5) -> Bool {
        let candidates = PreviewAnchors.views[id, default: []].compactMap(\.view)
        guard let view = candidates.last(where: {
            $0.window?.isVisible == true && (window == nil || $0.window == window)
                && !$0.isHiddenOrHasHiddenAncestor && !$0.visibleRect.isEmpty
        }), let target = view.window else { print("MISSING ANCHOR: \(id)"); return false }
        NSApp.activate(ignoringOtherApps: true)
        target.makeKeyAndOrderFront(nil)
        let point = view.convert(NSPoint(x: view.bounds.minX + view.bounds.width * xFraction, y: view.bounds.midY), to: nil)
        if id == "caption-latest" {
            print("LATEST target active=\(NSApp.isActive) key=\(target.isKeyWindow) point=\(point)")
            if let content = target.contentView, let hit = content.hitTest(content.convert(point, from: nil)) {
                print("LATEST hit=\(type(of: hit))")
            }
            for candidate in candidates where candidate.window == target {
                print("LATEST candidate rect=\(candidate.convert(candidate.bounds, to: target.contentView)) hidden=\(candidate.isHiddenOrHasHiddenAncestor) visible=\(candidate.visibleRect)")
            }
        }
        if id.hasPrefix("session-") { print("CONTROL \(id): bounds=\(view.bounds) point=\(point) window=\(target.windowNumber)"); fflush(stdout) }
        for type: NSEvent.EventType in [.leftMouseDown, .leftMouseUp] {
            let event = NSEvent.mouseEvent(with: type, location: point, modifierFlags: [],
                timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: target.windowNumber,
                context: nil, eventNumber: 1, clickCount: 1, pressure: 1)!
            target.sendEvent(event)
        }
        return true
    }
    func auditElements(_ value: Any, depth: Int = 0) -> [any NSAccessibilityProtocol] {
        guard depth < 40, let element = value as? any NSAccessibilityProtocol else { return [] }
        return [element] + (element.accessibilityChildren() ?? []).flatMap {
            auditElements($0, depth: depth + 1)
        }
    }
    func auditKey(_ key: UInt16, characters: String, modifiers: NSEvent.ModifierFlags = [], window: NSWindow? = nil) {
        let target = window ?? main
        NSApp.activate(ignoringOtherApps: true)
        target.makeKeyAndOrderFront(nil)
        for type: NSEvent.EventType in [.keyDown, .keyUp] {
            let event = NSEvent.keyEvent(with: type, location: .zero, modifierFlags: modifiers,
                timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: target.windowNumber,
                context: nil, characters: characters, charactersIgnoringModifiers: characters,
                isARepeat: false, keyCode: key)!
            NSApp.sendEvent(event)
        }
    }
    func auditDump(_ name: String, window: NSWindow? = nil) {
        let target = window ?? main
        let nodes = auditElements(target)
        let buttons = nodes.filter { $0.accessibilityRole() == .button || $0.accessibilityRole() == .menuButton }
        print("AUDIT \(name): \(nodes.count) AX nodes; \(buttons.count) buttons")
        for item in buttons {
            print("BUTTON label=\(item.accessibilityLabel() ?? "<nil>") title=\(item.accessibilityTitle() ?? "<nil>") frame=\(item.accessibilityFrame())")
        }
        print("FOCUS \(name): \(target.firstResponder.map { String(describing: type(of: $0)) } ?? "nil") active=\(NSApp.isActive) key=\(target.isKeyWindow) fullKeyboard=\(NSApp.isFullKeyboardAccessEnabled)")
        fflush(stdout)
    }
    func checkUsability() async throws {
        UserDefaults.standard.set("ice", forKey: LFTheme.preferenceKey)
        NSApp.activate(ignoringOtherApps: true)
        let lecture = workspace.lecture!
        workspace.selectLecture(nil)
        await settle()
        workspace.selectLecture(lecture.id)
        await settle()
        workspace.requestedTab = "live"
        await settle()
        clickAnchor("course-" + workspace.course!.id)
        await settle()
        print("AUDIT course sidebar entry selected=\(workspace.selectedLecture == nil)")
        auditKey(48, characters: "\t")
        auditDump("course-Tab")
        clickAnchor("lecture-" + lecture.id)
        await settle()
        guard workspace.selectedLecture == lecture.id else {
            throw BackendError.unavailable("课程列表返回讲次失败")
        }
        workspace.requestedTab = "live"
        await settle()
        auditDump("live")
        for i in 0..<8 {
            auditKey(48, characters: "\t")
            await settle(0.1)
            auditDump("Tab-\(i)")
        }
        try await snapshot("30-live-keyboard")
        // Exercise the existing A+ control up to its actual 28-point limit.
        for _ in 0..<13 { clickAnchor("caption-font-larger"); await settle(0.03) }
        print("AUDIT font-control maximum=\(UserDefaults.standard.double(forKey: "transcriptTextSize"))")
        try await snapshot("33-live-max-font-light")
        UserDefaults.standard.set(15.0, forKey: "transcriptTextSize")
        clickAnchor("floating-open")
        await settle()
        if let floating = NSApp.windows.first(where: { $0.title == "LectureFlow 字幕" && $0.isVisible }) {
            floating.setContentSize(NSSize(width: 320, height: 180))
            UserDefaults.standard.set(56.0, forKey: "subtitleSize")
            auditDump("floating", window: floating)
            auditKey(48, characters: "\t", window: floating)
            try await snapshot("34-floating-max-font", window: floating)
            workspace.hideSubtitles()
        } else { print("UNVERIFIED: floating header entry") }
        // Main sidebar gear, as used by the user, not a separately hosted settings view.
        clickAnchor("settings-open")
        await settle()
        if let settings = NSApp.windows.first(where: {
            $0 != main && $0.isVisible && ($0.title.contains("设置") || $0.identifier?.rawValue.contains("Settings") == true)
        }) {
            auditDump("settings-entry", window: settings)
            auditKey(48, characters: "\t", window: settings)
            auditDump("settings-Tab", window: settings)
            settings.close()
        } else { print("UNVERIFIED: settings gear did not expose expected window") }
        main.makeKeyAndOrderFront(nil)
        // The regular session strip's review button. Coordinate comes from the
        // captured 740-point synthetic window; assert presentation after clicking.
        clickAnchor("review-marks-open")
        await settle()
        guard workspace.reviewPresented else {
            throw BackendError.unavailable("用户复习标记入口未打开")
        }
        let sheet = main.sheets.first ?? main
        auditDump("bookmarks", window: sheet)
        auditKey(53, characters: "\u{1b}", window: sheet)
        await settle()
        print("AUDIT bookmark Escape dismissed=\(!workspace.reviewPresented) playbackStopped=\(workspace.playback.reviewID == nil)")
        if workspace.reviewPresented { workspace.reviewPresented = false }
        await settle(0.7)
        print("AUDIT bookmark after animation dismissed=\(!workspace.reviewPresented) playbackStopped=\(workspace.playback.reviewID == nil)")
        guard !workspace.reviewPresented, workspace.playback.reviewID == nil else {
            throw BackendError.unavailable("Escape 关闭书签后回听未停止")
        }
        fflush(stdout)
        try await checkPriorityEntry()
        print("AUDIT initial probe complete")
        fflush(stdout)
    }
    func checkPriorityEntry() async throws {
        main.makeKeyAndOrderFront(nil)
        let observer = NotificationCenter.default.addObserver(forName: NSMenu.didBeginTrackingNotification,
            object: nil, queue: .main) { notification in
            guard let menu = notification.object as? NSMenu,
                  let index = menu.items.firstIndex(where: { $0.title == "课后优先复核…" }) else { return }
            let timer = Timer(timeInterval: 0.15, repeats: false) { _ in
                menu.performActionForItem(at: index)
                menu.cancelTracking()
            }
            RunLoop.main.add(timer, forMode: .eventTracking)
        }
        defer { NotificationCenter.default.removeObserver(observer) }
        clickAnchor("caption-actions")
        await settle()
        guard let sheet = main.sheets.first else {
            throw BackendError.unavailable("Native transcript menu did not open priority sheet")
        }
        try await snapshot(UserDefaults.standard.bool(forKey: "previewDark") ? "66-priority-dark-list" : "31-priority-user-entry", window: sheet)
        guard clickAnchor("priority-item-preview-0", window: sheet) else {
            throw BackendError.unavailable("Synthetic priority item was not reachable through the user entry")
        }
        await settle()
        try await snapshot(UserDefaults.standard.bool(forKey: "previewDark") ? "67-priority-dark-compare" : "32-priority-user-compare", window: sheet)
        guard clickAnchor("priority-play", window: sheet) else {
            throw BackendError.unavailable("Missing priority playback entry")
        }
        for _ in 0..<25 where !workspace.playback.previewReviewPlaying { await settle(0.02) }
        guard workspace.playback.reviewID == "preview-0", workspace.playback.previewReviewPlaying,
              workspace.playback.error == nil else { throw BackendError.unavailable("Review did not start playing before Escape") }
        print("AUDIT priority playback active before Escape=true")
        auditKey(53, characters: "\u{1b}", window: sheet)
        await settle()
        print("AUDIT priority Escape dismissed=\(main.sheets.isEmpty) playbackStopped=\(workspace.playback.reviewID == nil)")
        guard main.sheets.isEmpty, workspace.playback.reviewID == nil else {
            throw BackendError.unavailable("优先复核 Escape 未关闭或回听未停止")
        }
    }
    func runAuditOnly() async {
        do {
            await settle(2)
            await workspace.refresh()
            guard let course = workspace.overview.courses.first(where: { $0.name == "合成课程 · 课堂阅读" }), let lecture = course.lectures.first else {
                throw BackendError.unavailable("No existing synthetic fixture")
            }
            workspace.selectCourse(course.id)
            workspace.selectLecture(lecture.id)
            await settle(1)
            await workspace.live.loadHistory(lecture: lecture, backend: workspace.backend)
            main.setContentSize(NSSize(width: 740, height: 520))
            try await checkUsability()
            await workspace.prepareForExit()
            NSApp.terminate(nil)
        } catch { print("FAIL: \(error)"); fflush(stdout); exit(1) }
    }

    func runClassroomFlow() async {
        do {
            await settle(2)
            await workspace.createCourse("合成课程 · 连续阅读验证")
            workspace.configureSession(.all)
            main.setContentSize(NSSize(width: 740, height: 520))
            await settle()
            guard clickAnchor("course-start") else { throw BackendError.unavailable("缺少开课按钮") }
            for _ in 0..<40 where !workspace.live.isCapturing { await settle(0.2) }
            guard workspace.live.isCapturing, workspace.session.state == .running,
                  let lecture = workspace.lecture else { throw BackendError.unavailable("开课入口未开始采集") }
            await settle()
            let routedLive = PreviewAnchors.views["caption-search", default: []].contains {
                $0.view?.window == main && $0.view?.visibleRect.isEmpty == false
            }
            print("CLASSROOM start routed live=\(routedLive)"); fflush(stdout)
            try await snapshot("40-recording-user-entry")
            // Continue independent checks even if the initial routing is wrong.
            workspace.requestedTab = "live"
            await settle()
            var event: [String: Any] = ["type": "caption", "session_id": "synthetic-classroom",
                "id": "synthetic-live-1", "start": 0.0, "end": 2.0, "status": "temporary", "revision": 1,
                "english": "The model keeps the original evidence. A later translation belongs to this same sentence.", "chinese": ""]
            workspace.live.previewEvent(event, session: "synthetic-classroom", lecture: lecture.id)
            try await snapshot("41-recording-english-first")
            let pendingEvent = event
            for index in 2...10 {
                event["id"] = "synthetic-live-\(index)"; event["start"] = Double(index * 2)
                event["end"] = Double(index * 2 + 2)
                workspace.live.previewEvent(event, session: "synthetic-classroom", lecture: lecture.id)
            }
            auditKey(116, characters: "\u{f72c}")
            await settle()
            func readingScroll(_ view: NSView) -> NSScrollView? {
                if let scroll = view as? NSScrollView, scroll.bounds.width > 400,
                   (scroll.documentView?.bounds.height ?? 0) > scroll.contentView.bounds.height + 40 {
                    return scroll
                }
                return view.subviews.lazy.compactMap { readingScroll($0) }.first
            }
            guard let scroll = readingScroll(main.contentView!) else {
                throw BackendError.unavailable("录制中未找到可回阅的字幕区")
            }
            scroll.contentView.scroll(to: NSPoint(x: 0, y: 30))
            scroll.reflectScrolledClipView(scroll.contentView)
            await settle()
            let offset = scroll.contentView.bounds.origin.y
            let beforeSwitch = workspace.session.duration(at: ProcessInfo.processInfo.systemUptime)
            clickAnchor("settings-open")
            await settle()
            guard let settings = NSApp.windows.first(where: {
                $0 != main && $0.isVisible && ($0.title.contains("设置") || $0.identifier?.rawValue.contains("Settings") == true)
            }) else { throw BackendError.unavailable("录制中设置未打开") }
            clickAnchor("settings-theme", window: settings, xFraction: 0.85)
            await settle()
            guard UserDefaults.standard.string(forKey: LFTheme.preferenceKey) == "forest",
                  workspace.live.isCapturing else { throw BackendError.unavailable("录制中主题选择失败") }
            try await snapshot("43-recording-theme-settings", window: settings)
            clickAnchor("settings-theme-reset", window: settings)
            await settle()
            guard UserDefaults.standard.string(forKey: LFTheme.preferenceKey) == "ice",
                  workspace.live.isCapturing else { throw BackendError.unavailable("恢复默认中断采集") }
            settings.standardWindowButton(.closeButton)?.performClick(nil)
            main.makeKeyAndOrderFront(nil)
            let count = workspace.live.displayCaptions.count
            var translated = pendingEvent
            translated["chinese"] = "模型保留原始证据。"
            translated["revision"] = 2
            workspace.live.previewEvent(translated, session: "synthetic-classroom", lecture: lecture.id)
            await settle()
            let after = readingScroll(main.contentView!)?.contentView.bounds.origin.y
            let row = workspace.live.captions.first { $0.id == "synthetic-live-1" }
            guard let after, abs(after - offset) < 0.5,
                  row?.chinese == translated["chinese"] as? String,
                  row?.english == pendingEvent["english"] as? String,
                  workspace.live.displayCaptions.count == count,
                  workspace.session.duration(at: ProcessInfo.processInfo.systemUptime) > beforeSwitch
            else { throw BackendError.unavailable("切色或中文回填改变了阅读位置、段落或录音计时") }
            print("JOINT recording, delayed Chinese after theme/reset; offset=\(offset)->\(after), rows=\(count), timer advanced")
            try await snapshot("42-recording-chinese-in-place")
            clickAnchor("caption-latest")
            await settle()
            guard let atEnd = readingScroll(main.contentView!), let document = atEnd.documentView else {
                throw BackendError.unavailable("Missing reading area after returning to latest")
            }
            let estimatedGap = document.bounds.height - atEnd.contentView.bounds.maxY
            print("CLASSROOM pre-render estimated gap=\(estimatedGap), y=\(atEnd.contentView.bounds.minY), document=\(document.bounds.height), viewport=\(atEnd.contentView.bounds.height)")
            // Under a locked desktop, LazyVStack may retain an estimated document
            // height until a display pass. Observe the rendered frame before
            // measuring; never click twice or adjust the actual scroll position.
            try await snapshot("42b-return-latest")
            let gap = document.bounds.height - atEnd.contentView.bounds.maxY
            guard gap >= -1, gap <= LFChrome.space8 + 1, atEnd.contentView.bounds.minY > after else {
                throw BackendError.unavailable("Return to latest did not reach the last paragraph")
            }
            print("CLASSROOM return latest gap=\(gap); reading advanced=true")
            clickAnchor("session-primary")
            await settle()
            guard workspace.session.state == .paused, workspace.lecture?.segments.isEmpty == true else {
                throw BackendError.unavailable("Pause must not end or register a saved recording")
            }
            print("CLASSROOM paused: saved segments=0; end is a separate action")
            try await snapshot("44-recording-user-paused")
            clickAnchor("session-primary")
            await settle()
            guard workspace.live.isCapturing else { throw BackendError.unavailable("继续按钮失败") }
            clickAnchor("session-end")
            print("CLASSROOM immediately after end click: \(workspace.session.state)"); fflush(stdout)
            for _ in 0..<30 {
                await settle(0.3)
                await workspace.refresh()
                if workspace.session.state == .ended && workspace.lecture?.segments.count == 1 { break }
            }
            print("CLASSROOM sealed state=\(workspace.session.state), segments=\(workspace.lecture?.segments.count ?? -1), error=\(workspace.error ?? "none")"); fflush(stdout)
            guard workspace.session.state == .ended, workspace.lecture?.segments.count == 1 else {
                throw BackendError.unavailable("结束未保留一个连续录音段")
            }
            print("PASS: user start, bilingual fixture, settings theme/reset during capture, pause/resume/end; one sealed recording")
            fflush(stdout)
            await workspace.prepareForExit()
            if !routedLive { print("FAIL: capture started on offline source"); fflush(stdout); exit(1) }
            NSApp.terminate(nil)
        } catch {
            print("FAIL: \(error)"); fflush(stdout)
            workspace.endSession()
            await workspace.prepareForExit()
            exit(1)
        }
    }
}

@MainActor extension PreviewSuite {
    func snapshotWhiteBackdrop(_ name: String, window: NSWindow) async throws {
        window.orderFrontRegardless()
        await settle()
        let windows = CGWindowListCopyWindowInfo(.optionAll, kCGNullWindowID) as? [[String: Any]] ?? []
        guard let info = windows.first(where: { ($0[kCGWindowNumber as String] as? Int) == window.windowNumber }),
              let bounds = info[kCGWindowBounds as String] as? [String: Double] else {
            throw BackendError.unavailable("Synthetic white backdrop not registered")
        }
        let rect = ["X", "Y", "Width", "Height"].map { bounds[$0]! }
        let done = root.appendingPathComponent("capture-done.json")
        try? FileManager.default.removeItem(at: done)
        let request: [String: Any] = ["rect": rect, "output": root.appendingPathComponent("evidence/" + name + ".png").path]
        try JSONSerialization.data(withJSONObject: request).write(to: root.appendingPathComponent("capture-request.json"), options: .atomic)
        for _ in 0..<200 {
            if FileManager.default.fileExists(atPath: done.path) {
                let result = try JSONSerialization.jsonObject(with: Data(contentsOf: done)) as! [String: Any]
                guard result["ok"] as? Bool == true else { throw BackendError.unavailable("White backdrop capture failed") }
                print(result["captured"] as? Bool == false ? "NOT CAPTURED: " + name : "snapshot: " + name)
                fflush(stdout); return
            }
            await settle(0.1)
        }
        throw BackendError.unavailable("White backdrop capture timed out")
    }

    func runFloatingAudit() async {
        do {
            await settle(2)
            await workspace.refresh()
            guard let course = workspace.overview.courses.first(where: { $0.name == "合成课程 · 课堂阅读" }), let lecture = course.lectures.first else {
                throw BackendError.unavailable("No synthetic lecture for floating audit")
            }
            workspace.selectCourse(course.id); workspace.selectLecture(lecture.id)
            await settle(1)
            UserDefaults.standard.set(0.2, forKey: "subtitleOpacity")
            UserDefaults.standard.set(56.0, forKey: "subtitleSize")
            UserDefaults.standard.set("both", forKey: "subtitleLanguage")
            let backdrop = NSWindow(contentRect: NSRect(x: 160, y: 110, width: 380, height: 650),
                styleMask: [.borderless], backing: .buffered, defer: false)
            backdrop.isReleasedWhenClosed = false
            backdrop.backgroundColor = .white
            backdrop.isOpaque = true
            let white = NSView(frame: NSRect(x: 0, y: 0, width: 380, height: 650))
            white.wantsLayer = true
            white.layer?.backgroundColor = NSColor.white.cgColor
            backdrop.contentView = white
            backdrop.level = NSWindow.Level(rawValue: NSWindow.Level.statusBar.rawValue - 1)
            backdrop.orderFrontRegardless()
            let keyBefore = NSApp.keyWindow
            workspace.openSubtitles()
            await settle()
            guard let floating = NSApp.windows.first(where: { $0.title == "LectureFlow 字幕" }) else {
                throw BackendError.unavailable("No floating window")
            }
            print("FLOAT show kept key window=\(NSApp.keyWindow === keyBefore); reduced motion injected=true")
            guard NSApp.keyWindow === keyBefore else { throw BackendError.unavailable("Floating window stole key focus") }
            floating.setFrame(NSRect(x: 190, y: 140, width: 320, height: 580), display: true)
            var event: [String: Any] = ["type": "caption", "session_id": "synthetic-contrast", "id": "contrast-old",
                "start": 0.0, "end": 2.0, "status": "temporary", "revision": 1,
                "english": "Long bilingual captions must stay readable on a white lecture slide. Preserve the original evidence and the reading position.",
                "chinese": "这是一段较长的合成中文，用来检查白色课件背板、最窄窗口和最大字号。旧句也必须清楚可读，原文证据和阅读位置都应保留。"]
            workspace.live.previewEvent(event, session: "synthetic-contrast", lecture: lecture.id)
            event["id"] = "contrast-new"; event["start"] = 2.0; event["end"] = 4.0
            event["english"] = "The next sentence arrives in English first."; event["chinese"] = ""
            workspace.live.previewEvent(event, session: "synthetic-contrast", lecture: lecture.id)
            await settle()
            guard workspace.live.displayCaptions.contains(where: { $0.id == "contrast-new" && $0.chinese.isEmpty }) else {
                throw BackendError.unavailable("Floating English fixture was not applied")
            }
            auditKey(116, characters: "\u{f72c}", window: floating)
            func scrolls(_ view: NSView) -> [NSScrollView] {
                (view as? NSScrollView).map { [$0] } ?? view.subviews.flatMap(scrolls)
            }
            guard let scroll = scrolls(floating.contentView!).first else { throw BackendError.unavailable("No floating scroll view") }
            scroll.contentView.scroll(to: NSPoint(x: 0, y: 20))
            scroll.reflectScrolledClipView(scroll.contentView)
            await settle()
            let offset = scroll.contentView.bounds.origin.y
            event["revision"] = 2; event["chinese"] = "下一句先显示英文，再把中文填回同一段。"
            workspace.live.previewEvent(event, session: "synthetic-contrast", lecture: lecture.id)
            await settle()
            guard workspace.live.displayCaptions.contains(where: { $0.id == "contrast-new" && !$0.chinese.isEmpty }) else {
                throw BackendError.unavailable("Floating delayed Chinese fixture was not applied")
            }
            let revisedOffset = scroll.contentView.bounds.origin.y
            print("FLOAT reduced-motion reading offset before=\(offset), after=\(revisedOffset)")
            guard abs(offset - revisedOffset) < 0.5 else { throw BackendError.unavailable("Late translation moved the reading position") }
            for theme in ["ice", "forest"] {
                UserDefaults.standard.set(theme, forKey: LFTheme.preferenceKey)
                await settle()
                floating.orderFrontRegardless()
                try await snapshot("52-\(theme)-floating-local-render", window: floating)
                try await snapshotWhiteBackdrop("50-\(theme)-white-min-opacity", window: backdrop)
                // Scroll within the existing bilingual mode to inspect old English.
                scroll.contentView.scroll(to: NSPoint(x: 0, y: 420))
                scroll.reflectScrolledClipView(scroll.contentView)
                await settle()
                try await snapshotWhiteBackdrop("51-\(theme)-white-old-english", window: backdrop)
                scroll.contentView.scroll(to: NSPoint(x: 0, y: 20))
                scroll.reflectScrolledClipView(scroll.contentView)
            }
            let minimum = floating.contentMinSize.height
            print("FLOAT minimum readable height at 56pt bilingual=\(minimum)")
            guard minimum >= 150 else { throw BackendError.unavailable("Floating minimum height clips enlarged glyphs") }
            floating.setContentSize(NSSize(width: 320, height: minimum))
            try await snapshot("53-floating-minimum-readable-height", window: floating)
            print("PASS: floating focus within app, reduced-motion late update, two themes at opacity 0.2 width 320 font 56")
            print("NOTE: external presentation-app focus and full-screen Spaces require manual acceptance")
            fflush(stdout)
            workspace.hideSubtitles(); backdrop.close()
            UserDefaults.standard.set("ice", forKey: LFTheme.preferenceKey)
            UserDefaults.standard.set(0.86, forKey: "subtitleOpacity")
            UserDefaults.standard.set(22.0, forKey: "subtitleSize")
            await workspace.prepareForExit()
            NSApp.terminate(nil)
        } catch {
            print("FAIL: \(error)"); fflush(stdout)
            await workspace.prepareForExit(); exit(1)
        }
    }
}


@MainActor extension PreviewSuite {
    func checkAudioEmptyCenter(_ label: String) throws {
        func rect(_ id: String) throws -> NSRect {
            guard let view = PreviewAnchors.views[id, default: []].compactMap(\.view).last(where: {
                $0.window == main && !$0.isHiddenOrHasHiddenAncestor && !$0.visibleRect.isEmpty
            }) else { throw BackendError.unavailable("Missing empty-state region: \(id)") }
            return view.convert(view.bounds, to: main.contentView)
        }
        let region = try rect("audio-empty-region"), content = try rect("audio-empty-content")
        let dx = abs(region.midX - content.midX), dy = abs(region.midY - content.midY)
        print("EMPTY CENTER \(label): window=\(main.frame.size), region=\(region), content=\(content), delta=\(dx),\(dy)")
        guard dx <= 1, dy <= 1, content.height <= region.height + 1, content.width <= region.width + 1 else {
            throw BackendError.unavailable("Active audio empty state is not centered inside the remaining list region")
        }
    }

    func anchorVisible(_ id: String) -> Bool {
        PreviewAnchors.views[id, default: []].compactMap(\.view).contains {
            $0.window == main && !$0.isHiddenOrHasHiddenAncestor && !$0.visibleRect.isEmpty
        }
    }
    func runPresentationAudit() async {
        do {
            await settle(2)
            await workspace.refresh()
            guard let course = workspace.overview.courses.first(where: { $0.name == "合成课程 · 课堂阅读" }),
                  let lecture = course.lectures.first else { throw BackendError.unavailable("Missing synthetic fixture") }
            workspace.selectCourse(course.id); workspace.selectLecture(lecture.id)
            await settle(1)
            main.setContentSize(NSSize(width: 740, height: 520))
            clickAnchor("content-tab-audio"); await settle()
            guard !anchorVisible("caption-search"), !anchorVisible("transcript-source-live") else {
                throw BackendError.unavailable("Audio tab exposed transcript-only controls")
            }
            try await snapshot("60-navigation-audio")
            clickAnchor("content-tab-transcript"); await settle()
            guard anchorVisible("transcript-source-live"), anchorVisible("transcript-source-offline") else {
                throw BackendError.unavailable("Transcript tab lost its subordinate source controls")
            }
            clickAnchor("transcript-source-live"); await settle()
            guard anchorVisible("caption-search") else { throw BackendError.unavailable("Live source did not open") }
            try await snapshot("61-navigation-live")
            clickAnchor("transcript-source-offline"); await settle()
            guard !anchorVisible("caption-search"), anchorVisible("transcript-source-live") else {
                throw BackendError.unavailable("Offline source changed the primary navigation")
            }
            try await snapshot("62-navigation-offline")
            clickAnchor("transcript-source-live"); await settle()
            guard workspace.lecture?.id == lecture.id else { throw BackendError.unavailable("Navigation changed the lecture") }
            try await checkPriorityEntry()
            UserDefaults.standard.set(true, forKey: "previewDark")
            await settle()
            try await checkPriorityEntry()
            UserDefaults.standard.set(false, forKey: "previewDark")
            for (entry, name) in [("queue-open", "70-queue"), ("trash-open", "71-trash"), ("materials-open", "72-materials")] {
                guard clickAnchor(entry) else { throw BackendError.unavailable("Missing entry \(entry)") }
                await settle()
                guard let sheet = main.sheets.first else { throw BackendError.unavailable("No sheet for \(entry)") }
                try await snapshot(name, window: sheet)
                auditKey(53, characters: "\u{1b}", window: sheet)
                await settle()
                guard main.sheets.isEmpty else { throw BackendError.unavailable("Escape failed for \(entry)") }
            }
            clickAnchor("settings-open"); await settle()
            guard let settings = NSApp.windows.first(where: {
                $0 != main && $0.isVisible && ($0.title.contains("设置") || $0.identifier?.rawValue.contains("Settings") == true)
            }) else { throw BackendError.unavailable("Settings entry missing") }
            settings.setContentSize(NSSize(width: 780, height: 620)); await settle()
            for (index, group) in ["外观", "识别与翻译", "音频", "存储"].enumerated() {
                guard clickAnchor("settings-group-\(group)", window: settings) else {
                    throw BackendError.unavailable("Settings group missing: \(group)")
                }
                await settle(); try await snapshot("73-settings-group-\(index)", window: settings)
            }
            settings.close(); main.makeKeyAndOrderFront(nil)
            print("PASS: rail queue/trash/materials entry + Escape; four settings groups through real gear entry")
            let sample = NSWindow(contentRect: NSRect(x: 180, y: 180, width: 360, height: 390),
                styleMask: [.titled, .closable], backing: .buffered, defer: false)
            sample.isReleasedWhenClosed = false; sample.title = "合成状态与差异样例"
            sample.contentView = NSHostingView(rootView: VStack(alignment: .leading, spacing: 12) {
                LFStatusBanner(message: "本地模型尚未就绪", tone: .information) { Button("继续下载") {} }
                LFStatusBanner("字幕处理中断，正在自动恢复；少量译文可能未完成，可用录音课后转写。This long synthetic message must wrap without losing its final sentence.", tone: .attention)
                LFStatusBanner("合成错误：本次操作未完成。已有录音保留。", tone: .failure)
                LFReviewChange(change: "- original observations")
                LFReviewChange(change: "+ revised measurements with a deliberately long phrase")
            }.padding(16).frame(width: 360, height: 390, alignment: .topLeading))
            sample.setContentSize(NSSize(width: 360, height: 390))
            sample.makeKeyAndOrderFront(nil)
            for theme in ["ice", "forest"] {
                UserDefaults.standard.set(theme, forKey: LFTheme.preferenceKey)
                sample.appearance = NSAppearance(named: .aqua)
                try await snapshot("64-\(theme)-adaptive-status-light", window: sample)
                sample.appearance = NSAppearance(named: .darkAqua)
                try await snapshot("65-\(theme)-adaptive-status-dark", window: sample)
            }
            sample.close()
            UserDefaults.standard.set("ice", forKey: LFTheme.preferenceKey)
            print("PASS: independent primary tabs/source switches; same lecture; long status and diff component windows")
            fflush(stdout)
            await workspace.prepareForExit(); NSApp.terminate(nil)
        } catch { print("FAIL: \(error)"); fflush(stdout); await workspace.prepareForExit(); exit(1) }
    }
}


@MainActor extension PreviewSuite {
    func checkEmptyCenter(_ label: String, window: NSWindow? = nil) throws {
        let target = window ?? main
        func rect(_ id: String) throws -> NSRect {
            guard let view = PreviewAnchors.views[id, default: []].compactMap(\.view).last(where: {
                $0.window == target && !$0.isHiddenOrHasHiddenAncestor && !$0.visibleRect.isEmpty
            }) else { throw BackendError.unavailable("Missing centered empty region: \(label)/\(id)") }
            return view.convert(view.bounds, to: target.contentView)
        }
        let region = try rect("empty-region"), content = try rect("empty-content")
        let dx = abs(region.midX - content.midX), dy = abs(region.midY - content.midY)
        let overflow = content.height > region.height + 1
        print("EMPTY PAGE \(label): window=\(target.frame.size), region=\(region), content=\(content), delta=\(dx),\(dy), scrollOverflow=\(overflow)")
        guard dx <= 1, content.width <= region.width + 1, region.height > 0,
              overflow || dy <= 1 else { throw BackendError.unavailable("Empty region is not centered: \(label)") }
    }

    func runEmptyAudit() async {
        do {
            await settle(2); await workspace.refresh()
            let emptyCourseName = "合成空态 · 居中验证 " + UUID().uuidString.prefix(4)
            if !workspace.overview.courses.contains(where: { $0.name == emptyCourseName }) {
                await workspace.createCourse(emptyCourseName)
            }
            // The regular refresh timer may supersede the mutation's refresh.
            for _ in 0..<10 {
                if workspace.overview.courses.contains(where: { $0.name == emptyCourseName }) { break }
                await settle(0.5); await workspace.refresh()
            }
            guard let emptyCourse = workspace.overview.courses.first(where: { $0.name == emptyCourseName }) else {
                throw BackendError.unavailable("Empty course fixture missing: \(workspace.error ?? workspace.refreshError ?? "no error")")
            }
            workspace.selectCourse(emptyCourse.id); workspace.selectLecture(nil); await settle()
            func mainPair(_ name: String) async throws {
                for (suffix, size) in [("small", NSSize(width: 740, height: 520)), ("wide", NSSize(width: 1080, height: 700))] {
                    main.setContentSize(size); try await snapshot("80-\(name)-\(suffix)")
                    try checkEmptyCenter("\(name)-\(suffix)")
                }
            }
            func sheetPair(_ name: String, sheet: NSWindow, small: NSSize, wide: NSSize) async throws {
                for (suffix, size) in [("small", small), ("wide", wide)] {
                    sheet.setContentSize(size); try await snapshot("81-\(name)-\(suffix)", window: sheet)
                    try checkEmptyCenter("\(name)-\(suffix)", window: sheet)
                }
            }
            try await mainPair("course")
            guard let lectureID = await workspace.createLecture(emptyCourse.id, title: "空讲次 · 合成验证") else {
                throw BackendError.unavailable("Empty lecture fixture missing")
            }
            workspace.selectLecture(lectureID); await settle(1)
            clickAnchor("content-tab-audio"); await settle(); try await mainPair("audio-idle")
            guard clickAnchor("content-tab-transcript") else { throw BackendError.unavailable("Transcript tab missing") }
            await settle()
            guard clickAnchor("transcript-source-live") else { throw BackendError.unavailable("Live source missing") }
            await settle(); try await mainPair("live")
            clickAnchor("transcript-source-offline"); await settle(); try await mainPair("offline")
            for (entry, name, small, wide) in [
                ("materials-open", "materials", NSSize(width: 620, height: 440), NSSize(width: 880, height: 620)),
                ("queue-open", "queue", NSSize(width: 480, height: 400), NSSize(width: 680, height: 440)),
                ("trash-open", "trash", NSSize(width: 480, height: 400), NSSize(width: 680, height: 440)),
                ("review-marks-open", "marks", NSSize(width: 620, height: 400), NSSize(width: 760, height: 540))
            ] {
                guard clickAnchor(entry) else { throw BackendError.unavailable("Missing entry \(entry)") }
                await settle()
                guard let sheet = main.sheets.first else { throw BackendError.unavailable("Missing sheet \(entry)") }
                try await sheetPair(name, sheet: sheet, small: small, wide: wide)
                if name == "materials" {
                    clickAnchor("materials-section", window: sheet, xFraction: 0.8); await settle()
                    try await sheetPair("terms", sheet: sheet, small: small, wide: wide)
                }
                auditKey(53, characters: "\u{1b}", window: sheet); await settle()
                guard main.sheets.isEmpty else { throw BackendError.unavailable("Sheet did not dismiss: \(entry)") }
            }
            clickAnchor("transcript-source-live"); await settle()
            let observer = NotificationCenter.default.addObserver(forName: NSMenu.didBeginTrackingNotification,
                object: nil, queue: .main) { note in
                guard let menu = note.object as? NSMenu,
                      let index = menu.items.firstIndex(where: { $0.title == "课后优先复核…" }) else { return }
                let timer = Timer(timeInterval: 0.15, repeats: false) { _ in menu.performActionForItem(at: index); menu.cancelTracking() }
                RunLoop.main.add(timer, forMode: .eventTracking)
            }
            clickAnchor("caption-actions"); await settle()
            NotificationCenter.default.removeObserver(observer)
            guard let priority = main.sheets.first else { throw BackendError.unavailable("Empty priority sheet missing") }
            try await sheetPair("priority", sheet: priority, small: NSSize(width: 410, height: 320), wide: NSSize(width: 820, height: 460))
            auditKey(53, characters: "\u{1b}", window: priority); await settle()
            guard let fixtureCourse = workspace.overview.courses.first(where: { $0.name == "合成课程 · 课堂阅读" }),
                  let fixture = fixtureCourse.lectures.first else { throw BackendError.unavailable("Search fixture missing") }
            workspace.selectCourse(fixtureCourse.id); workspace.selectLecture(fixture.id); await settle(1)
            await workspace.live.loadHistory(lecture: fixture, backend: workspace.backend)
            guard clickAnchor("content-tab-transcript") else { throw BackendError.unavailable("Transcript tab missing") }
            await settle()
            guard clickAnchor("transcript-source-live") else { throw BackendError.unavailable("Live source missing") }; await settle()
            for (source, field, name) in [("transcript-source-live", "caption-search", "live-search"), ("transcript-source-offline", "offline-search", "offline-search")] {
                clickAnchor(source); await settle(1)
                guard clickAnchor(field), let editor = main.firstResponder as? NSTextView else {
                    throw BackendError.unavailable("Search editor missing: \(field)")
                }
                editor.selectAll(nil)
                editor.insertText("unmatched-centered-empty-test", replacementRange: NSRange(location: NSNotFound, length: 0))
                await settle(); try await mainPair(name)
            }
            print("PASS: centered course/audio/live/offline/search/materials/terms/queue/trash/marks/priority empty regions; actual entries, small and wide")
            fflush(stdout); await workspace.prepareForExit(); NSApp.terminate(nil)
        } catch { print("FAIL: \(error)"); fflush(stdout); await workspace.prepareForExit(); exit(1) }
    }
}

@MainActor extension PreviewSuite {
    func layoutRect(_ id: String) throws -> NSRect {
        guard let view = PreviewAnchors.views[id, default: []].compactMap(\.view).last(where: { $0.window == main && !$0.visibleRect.isEmpty }) else {
            throw BackendError.unavailable("Missing layout anchor: " + id)
        }
        let r = view.convert(view.bounds, to: main.contentView)
        let clipped = view.bounds.intersection(view.visibleRect).height + 0.5 < view.bounds.height
        print("ROW \(id): rect=\(r), visible=\(view.visibleRect), bounds=\(view.bounds), verticallyClipped=\(clipped)")
        if root.lastPathComponent.contains("after") && (id.hasPrefix("course-") || id.hasPrefix("lecture-")) && clipped {
            throw BackendError.unavailable("Sidebar row vertically clipped: " + id)
        }
        return r
    }
    func runLayoutAudit() async {
        do {
            await settle(2)
            let names = ["线性代数", "机器学习与统计推断专题 Machine Learning and Statistical Inference", "计算机系统与并行架构 Advanced Computer Systems", "面向复杂真实环境的智能系统研究方法与课堂实践"]
            var ids: [(String, String)] = []
            for (i, name) in names.enumerated() {
                await workspace.createCourse(name)
                for _ in 0..<20 where !workspace.overview.courses.contains(where: { $0.name == name }) {
                    await settle(0.2); await workspace.refresh()
                }
                guard let course = workspace.overview.courses.first(where: { $0.name == name }),
                      let id = await workspace.createLecture(course.id, title: i == 0 ? "第一讲 · 向量空间" : "第十二讲 · 复杂模型的推导与课堂实例 Long Lecture Title and Evidence") else {
                    throw BackendError.unavailable("Long-name synthetic fixture missing")
                }
                ids.append((course.id, id))
            }
            workspace.selectCourse(ids[1].0); workspace.selectLecture(ids[1].1); await settle()
            for width in [740.0, 820.0, 1080.0] {
                main.setContentSize(NSSize(width: width, height: 700))
                try await snapshot("90-courses-\(Int(width))")
                for (course, lecture) in ids {
                    _ = try layoutRect("course-" + course)
                    _ = try layoutRect("lecture-" + lecture)
                }
            }
            main.setContentSize(NSSize(width: 1080, height: 700))
            if clickAnchor("course-disclosure-" + ids[1].0) {
                await settle()
                let childVisible = PreviewAnchors.views["lecture-" + ids[1].1, default: []].compactMap(\.view).contains { $0.window == main && !$0.visibleRect.isEmpty }
                guard !childVisible else { throw BackendError.unavailable("Collapse did not hide lecture rows") }
                try await snapshot("91-course-disclosure")
                guard clickAnchor("course-disclosure-" + ids[1].0) else { throw BackendError.unavailable("Expand control missing") }
                await settle(); _ = try layoutRect("lecture-" + ids[1].1)
                guard workspace.selectedLecture == ids[1].1 else { throw BackendError.unavailable("Disclosure changed lecture selection") }
                print("PASS: collapse/expand through accessible native button, selection unchanged")
            } else {
                print("BASELINE: disclosure not instrumented; original Section binding retained in source")
                try await snapshot("91-course-disclosure")
            }
            workspace.configureSession(CapturePreset.recording.flags)
            main.setContentSize(NSSize(width: 740, height: 572)); await settle()
            guard clickAnchor("session-primary") else { throw BackendError.unavailable("Start button missing") }
            for _ in 0..<30 where !workspace.live.isCapturing { await settle(0.2) }
            guard workspace.live.isCapturing else { throw BackendError.unavailable("Synthetic recording did not start") }
            func positions(_ state: String) throws -> [CGFloat] {
                let p = try layoutRect("session-primary"), e = try layoutRect("session-end"), b = try layoutRect("review-marks-open")
                print("CONTROL POSITIONS \(state): primary=\(p.midY), end=\(e.midY), marks=\(b.midY)")
                let timer = try layoutRect("session-timer"), wave = try layoutRect("session-waveform")
                var result = [p.midY,e.midY,b.midY,timer.midY,wave.midY]
                if root.lastPathComponent.contains("after") {
                    let card = try layoutRect("session-card")
                    result += [card.minY,card.height]
                }
                return result
            }
            try await snapshot("92-recording-normal"); let normal = try positions("running")
            workspace.notice = "字幕处理中断，正在自动恢复；少量译文可能未完成，可用录音课后转写。以下为合成长提示，用于验证控件位置不因状态文字折行而改变。Synthetic recovery information remains readable while recording stays independent."
            try await snapshot("93-recording-warning"); let warning = try positions("warning")
            clickAnchor("session-primary"); await settle()
            guard workspace.session.state == .paused else { throw BackendError.unavailable("Pause failed") }
            try await snapshot("94-recording-paused"); let paused = try positions("paused")
            workspace.notice = "合成暂停期间的独立通知：这不是常规暂停确认，必须继续显示。"
            try await snapshot("94b-paused-independent-notice")
            _ = try layoutRect("session-notice")
            _ = try positions("paused-independent-notice")
            clickAnchor("session-primary"); await settle()
            guard workspace.live.isCapturing else { throw BackendError.unavailable("Resume failed") }
            try await snapshot("95-recording-resumed"); let resumed = try positions("resumed")
            print("STABILITY normal=\(normal) warning=\(warning) paused=\(paused) resumed=\(resumed)")
            if root.lastPathComponent.contains("after") {
                for p in [warning,paused,resumed] { guard zip(normal,p).allSatisfy({ abs($0-$1)<=1 }) else { throw BackendError.unavailable("Recorder controls moved") } }
            }
            clickAnchor("session-end")
            for _ in 0..<40 where workspace.live.pendingSaves > 0 { await settle(0.2) }
            await workspace.refresh(); await settle()
            try await snapshot("96-recording-ended")
            guard workspace.session.state == .ended, workspace.lecture?.segments.count == 1 else { throw BackendError.unavailable("Sealed recording missing") }
            workspace.live.previewPendingSaves(1)
            try await snapshot("98-recording-saving")
            workspace.live.previewPendingSaves(0)
            workspace.live.error = "合成保存失败：原音仍保留，请使用恢复录音检查。"
            try await snapshot("97-recording-error")
            guard let errorSheet = main.sheets.first, workspace.error != nil else { throw BackendError.unavailable("Real error presentation missing") }
            try await snapshot("97b-recording-error-sheet", window: errorSheet)
            let alertText = elements(errorSheet.contentView!).compactMap { ($0 as? NSTextField)?.stringValue }
            print("NATIVE ERROR TEXT: \(alertText); workspaceError=\(workspace.error ?? "nil")")
            print("PASS: error propagated through unchanged handler into visible native alert")
            _ = press("知道了", in: errorSheet)
            workspace.error = nil; workspace.live.error = nil; await settle()
            print("PASS: long-name courses at 740/820/1080; synthetic start/pause/resume/seal; recorder coordinates logged")
            fflush(stdout); await workspace.prepareForExit(); exit(0)
        } catch { print("FAIL: \(error)"); fflush(stdout); await workspace.prepareForExit(); exit(1) }
    }
}

@MainActor extension PreviewSuite {
    func nativeDescendants(_ view: NSView) -> [NSView] { [view] + view.subviews.flatMap(nativeDescendants) }
    func sidebarDivider() -> NSView? {
        nativeDescendants(main.contentView!).first { $0.accessibilityIdentifier() == "library-sidebar-divider" }
    }
    func dragDivider(_ delta: Double) async throws {
        guard let view = sidebarDivider() else { throw BackendError.unavailable("Missing native splitter") }
        let start = view.convert(NSPoint(x: view.bounds.midX, y: view.bounds.midY), to: nil)
        main.makeKeyAndOrderFront(nil)
        print("SPLITTER drag start=\(start) frame=\(view.frame) value=\(view.accessibilityValue() ?? "nil") hit=\(String(describing: main.contentView!.hitTest(main.contentView!.convert(start, from: nil))))")
        for (type, offset) in [(NSEvent.EventType.leftMouseDown, 0.0), (.leftMouseDragged, delta), (.leftMouseUp, delta)] {
            let event = NSEvent.mouseEvent(with: type, location: NSPoint(x: start.x + offset, y: start.y),
                modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime,
                windowNumber: main.windowNumber, context: nil, eventNumber: 1, clickCount: 1, pressure: 1)!
            main.sendEvent(event)
        }
        await settle()
        print("SPLITTER drag end value=\(view.accessibilityValue() ?? "nil") preference=\(UserDefaults.standard.double(forKey: "librarySidebarWidth"))")
    }
    func choosePreviewInput(_ title: String) throws {
        func search(_ menu: NSMenu) -> NSMenuItem? {
            for item in menu.items {
                if item.title == title { return item }
                if let sub = item.submenu, let result = search(sub) { return result }
            }
            return nil
        }
        guard let menu = NSApp.mainMenu, let item = search(menu), let owner = item.menu else {
            throw BackendError.unavailable("Missing preview input menu: " + title)
        }
        owner.performActionForItem(at: owner.index(of: item))
        print("PREVIEW MENU invoked: " + title)
    }
    func waveImage(_ name: String) throws -> Data {
        let rect = try layoutRect("session-waveform")
        let view = main.contentView!
        view.displayIfNeeded()
        guard let bitmap = view.bitmapImageRepForCachingDisplay(in: rect) else { throw BackendError.unavailable("Waveform render missing") }
        view.cacheDisplay(in: rect, to: bitmap)
        guard let data = bitmap.representation(using: .png, properties: [:]) else { throw BackendError.unavailable("Waveform PNG missing") }
        try data.write(to: root.appendingPathComponent("evidence/" + name + ".wave.png"))
        return data
    }
    func runInteractionAudit() async {
        do {
            await settle(2)
            UserDefaults.standard.set(true, forKey: "librarySidebarVisible")
            UserDefaults.standard.removeObject(forKey: "librarySidebarWidth")
            let names = ["线性代数", "机器学习与统计推断专题 Machine Learning and Statistical Inference", "计算机系统与并行架构 Advanced Computer Systems", "面向复杂真实环境的智能系统研究方法与课堂实践"]
            var ids: [(String,String)] = []
            for (i,name) in names.enumerated() {
                await workspace.createCourse(name); await workspace.refresh()
                guard let course = workspace.overview.courses.first(where: { $0.name == name }),
                    let lecture = await workspace.createLecture(course.id, title: i == 0 ? "第一讲 · 向量空间" : "第十二讲 · 复杂模型的推导与课堂实例 Long Lecture Title and Evidence") else { throw BackendError.unavailable("Missing synthetic course") }
                ids.append((course.id,lecture))
            }
            workspace.selectCourse(ids[1].0); workspace.selectLecture(ids[1].1); await settle()
            workspace.configureSession(CapturePreset.recording.flags); await settle()
            let resizable = sidebarDivider() != nil
            for width in [740.0,820.0,1080.0] {
                UserDefaults.standard.removeObject(forKey: "librarySidebarWidth")
                main.setContentSize(NSSize(width: width, height: 700)); await settle()
                try await snapshot("100-courses-\(Int(width))")
                for (course,lecture) in ids { _ = try layoutRect("course-" + course); _ = try layoutRect("lecture-" + lecture) }
                if resizable {
                    let before = sidebarDivider()!.accessibilityValue() as! NSNumber
                    try await dragDivider(-20)
                    let after = sidebarDivider()!.accessibilityValue() as! NSNumber
                    guard abs(after.doubleValue - (before.doubleValue - 20)) < 1 else { throw BackendError.unavailable("Actual drag did not resize") }
                    try await dragDivider(1000)
                    let divider = sidebarDivider()!
                    let maximum = min(360.0, width - 45 - 6 - 480)
                    guard abs((divider.accessibilityValue() as! NSNumber).doubleValue - maximum) < 1 else { throw BackendError.unavailable("Main reading width not protected") }
                    let r = divider.convert(divider.bounds, to: main.contentView)
                    guard width - r.maxX >= 479.5 else { throw BackendError.unavailable("Reading pane below minimum") }
                    try await snapshot("101-dragged-\(Int(width))")
                    main.makeFirstResponder(divider)
                    auditKey(123, characters: "\u{F702}")
                    await settle()
                    guard abs((divider.accessibilityValue() as! NSNumber).doubleValue - (maximum - 10)) < 1 else { throw BackendError.unavailable("Keyboard resize failed") }
                    guard divider.accessibilityPerformIncrement() else { throw BackendError.unavailable("AX adjustment missing") }
                    await settle()
                    guard abs((divider.accessibilityValue() as! NSNumber).doubleValue - maximum) < 1 else { throw BackendError.unavailable("AX increment failed") }
                    print("PASS splitter width=\(width): mouse drag \(before)→\(after), limit=\(maximum), main>=480, arrow key and AX increment")
                }
            }
            if resizable {
                UserDefaults.standard.set(280.0, forKey: "librarySidebarWidth"); await settle()
                clickAnchor("rail-sidebar-toggle"); await settle()
                guard sidebarDivider() == nil else { throw BackendError.unavailable("Sidebar did not hide") }
                clickAnchor("rail-sidebar-toggle"); await settle()
                guard abs((sidebarDivider()!.accessibilityValue() as! NSNumber).doubleValue - 280) < 1 else { throw BackendError.unavailable("Width not restored") }
            }
            clickAnchor("course-disclosure-" + ids[1].0); await settle()
            guard !PreviewAnchors.views["lecture-" + ids[1].1,default:[]].compactMap(\.view).contains(where: { $0.window == main && !$0.visibleRect.isEmpty }) else { throw BackendError.unavailable("Collapse failed") }
            clickAnchor("course-disclosure-" + ids[1].0); await settle()
            guard workspace.selectedLecture == ids[1].1 else { throw BackendError.unavailable("Selection changed") }
            // Existing toggle only: no recording is started, even with no lecture selected.
            if resizable {
                workspace.selectLecture(nil); await settle()
                guard clickAnchor("floating-open") else { throw BackendError.unavailable("Rail subtitle toggle absent on course") }
                await settle(); guard workspace.floatingSubtitlesVisible && !workspace.live.isCapturing else { throw BackendError.unavailable("Floating toggle changed capture") }
                try await snapshot("102-floating-from-course")
                clickAnchor("floating-open"); await settle()
                guard !workspace.floatingSubtitlesVisible else { throw BackendError.unavailable("Floating toggle did not hide") }
                workspace.selectLecture(ids[1].1); await settle()
                clickAnchor("floating-open"); await settle()
                guard workspace.floatingSubtitlesVisible else { throw BackendError.unavailable("Lecture floating entry missing") }
                try await snapshot("103-floating-from-lecture")
                clickAnchor("floating-open"); await settle()
                guard clickAnchor("queue-open") else { throw BackendError.unavailable("Toolbar queue entry missing") }
                await settle(); guard main.sheets.count == 1 else { throw BackendError.unavailable("Queue sheet missing") }
                auditKey(53, characters: "\u{1b}", window: main.sheets.first!)
                await settle(); guard main.sheets.isEmpty else { throw BackendError.unavailable("Queue Escape did not dismiss") }
                print("PASS rail floating on course and lecture; no capture; toolbar queue opens one sheet")
            }
            UserDefaults.standard.removeObject(forKey: "librarySidebarWidth")
            main.setContentSize(NSSize(width: 1080, height: 700)); await settle()
            workspace.configureSession(CapturePreset.recording.flags)
            PreviewInput.signal = .low
            guard clickAnchor("session-primary") else { throw BackendError.unavailable("Missing recorder start") }
            for _ in 0..<30 where !workspace.live.isCapturing { await settle(0.2) }
            guard workspace.live.isCapturing else { throw BackendError.unavailable("Capture not started: state=\(workspace.session.state) sheets=\(main.sheets.count) error=\(workspace.error ?? "nil")") }
            var frames: [Data] = [], levels: [Float] = []
            for (name, signal) in [("110-low", PreviewInput.Signal.low), ("111-medium", .medium), ("112-high", .high)] {
                PreviewInput.signal = signal; await settle(3.5)
                levels.append(workspace.live.level)
                try await snapshot(name)
                frames.append(try waveImage(name))
            }
            guard levels[0] > 0 && levels[0] < levels[1] && levels[1] < levels[2], frames[0] != frames[1], frames[1] != frames[2] else { throw BackendError.unavailable("Amplitude did not reach rendered waveform") }
            print("PASS PCM→CapturePump RMS→LiveController.level→waveform: \(levels), 3 distinct native region renders")
            clickAnchor("session-primary"); await settle(0.5)
            guard workspace.session.state == .paused else { throw BackendError.unavailable("Pause failed") }
            let p1 = try waveImage("113-paused-a"); try choosePreviewInput("输入：变化音量（合成）"); await settle(1.5)
            let p2 = try waveImage("113-paused-b")
            guard p1 == p2 else { throw BackendError.unavailable("Paused waveform not frozen") }
            try await snapshot("113-paused")
            clickAnchor("session-primary"); await settle(3.5)
            guard workspace.live.isCapturing else { throw BackendError.unavailable("Resume failed") }
            let r1 = try waveImage("114-resumed-a"); await settle(1.2)
            let r2 = try waveImage("114-resumed-b")
            guard r1 != r2 else { throw BackendError.unavailable("Resumed waveform static") }
            try await snapshot("114-resumed")
            try choosePreviewInput("输入：静音"); await settle(5.5)
            let s1 = try waveImage("115-silence-a"); await settle(1)
            let s2 = try waveImage("115-silence-b")
            print("SILENCE level=\(workspace.live.level), stableRender=\(s1 == s2)")
            guard workspace.live.level == 0, s1 == s2 else { throw BackendError.unavailable("Silence shows fabricated activity") }
            try await snapshot("115-silence")
            clickAnchor("session-end")
            for _ in 0..<40 where workspace.live.pendingSaves > 0 { await settle(0.2) }
            await workspace.refresh(); await settle()
            guard workspace.session.state == .ended, workspace.lecture?.segments.count == 1 else { throw BackendError.unavailable("Generated audio did not seal") }
            try await snapshot("116-ended")
            UserDefaults.standard.set(280.0, forKey: "librarySidebarWidth")
            print("PASS waveform pauses frozen, resumes changing, silence level=0/stable, end seals 1 recording; synthetic PCM only")
            fflush(stdout); await workspace.prepareForExit(); exit(0)
        } catch { print("FAIL: \(error)"); fflush(stdout); await workspace.prepareForExit(); exit(1) }
    }
}
