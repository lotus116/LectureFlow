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
        if id.hasPrefix("session-") { print("CONTROL \(id): bounds=\(view.bounds) point=\(point) window=\(target.windowNumber)"); fflush(stdout) }
        for type: NSEvent.EventType in [.leftMouseDown, .leftMouseUp] {
            let event = NSEvent.mouseEvent(with: type, location: point, modifierFlags: [],
                timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: target.windowNumber,
                context: nil, eventNumber: 1, clickCount: 1, pressure: 1)!
            NSApp.sendEvent(event)
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
            clickAnchor("session-primary")
            await settle()
            guard workspace.session.state == .paused else { throw BackendError.unavailable("暂停按钮失败") }
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
