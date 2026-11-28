import AppKit
import AVFoundation
import LectureFlowCore
import SwiftUI

// Compiled only into the copied preview app by preview-frontend.py.
@main struct FrontendPreview: App {
    @StateObject private var workspace: Workspace
    @AppStorage("previewDark") private var dark = false
    @NSApplicationDelegateAdaptor(LectureFlowDelegate.self) private var delegate

    init() {
        let root = Bundle.main.object(forInfoDictionaryKey: "PreviewRoot") as! String
        setenv("LECTUREFLOW_PROJECT_ROOT", root + "/runtime", 1)
        let sourceRoot = Bundle.main.object(forInfoDictionaryKey: "PreviewSourceRoot") as! String
        setenv("PYTHONPATH", sourceRoot + "/src", 1)
        setenv("PYTHONDONTWRITEBYTECODE", "1", 1)
        let library: String
        if CommandLine.arguments.contains("--suite") {
            UserDefaults.standard.set("ice", forKey: "colorTheme")
            library = root + "/runtime/Library-" + UUID().uuidString
            UserDefaults.standard.set(library, forKey: "previewLibraryPath")
        } else {
            library = UserDefaults.standard.string(forKey: "previewLibraryPath") ?? root + "/runtime/Library"
        }
        setenv("LECTUREFLOW_LIBRARY_ROOT", library, 1)
        UserDefaults.standard.set(true, forKey: "modelDownloadPaused")
        UserDefaults.standard.set(false, forKey: "autoFloatingSubtitles")
        UserDefaults.standard.set(false, forKey: "previewDark")
        UserDefaults.standard.set(15.0, forKey: "transcriptTextSize")
        _workspace = StateObject(wrappedValue: Workspace())
    }
    var body: some Scene {
        Window("LectureFlow · 合成预览", id: "main") {
            LibraryView(model: workspace)
                .preferredColorScheme(dark ? .dark : .light)
                .frame(minWidth: LFChrome.windowMinWidth, minHeight: LFChrome.windowMinHeight)
                .onAppear {
                    delegate.workspace = workspace
                    if let logo = Bundle.main.url(forResource: "logo", withExtension: "png"),
                       let image = NSImage(contentsOf: logo) { NSApp.applicationIconImage = image }
                    if CommandLine.arguments.contains("--suite") {
                        Task { await PreviewSuite(workspace).run() }
                    }
                    #if THEME_PREVIEW
                    if CommandLine.arguments.contains("--floating-audit") {
                        Task { await PreviewSuite(workspace).runFloatingAudit() }
                    }
                    if CommandLine.arguments.contains("--presentation-audit") {
                        Task { await PreviewSuite(workspace).runPresentationAudit() }
                    }
                    if CommandLine.arguments.contains("--classroom-flow") {
                        Task { await PreviewSuite(workspace).runClassroomFlow() }
                    }
                    if CommandLine.arguments.contains("--audit-only") {
                        Task { await PreviewSuite(workspace).runAuditOnly() }
                    }
                    if CommandLine.arguments.contains("--theme-controls") {
                        Task { await PreviewSuite(workspace).checkThemeControls() }
                    }
                    if CommandLine.arguments.contains("--verify-theme-persistence") {
                        Task {
                            try? await Task.sleep(for: .seconds(1))
                            guard UserDefaults.standard.string(forKey: LFTheme.preferenceKey) == "forest" else {
                                print("FAIL: theme did not persist across process restart"); fflush(stdout); exit(1)
                            }
                            UserDefaults.standard.set("ice", forKey: LFTheme.preferenceKey)
                            print("PASS: persisted forest theme after restart; reset preview to ice")
                            fflush(stdout)
                            await workspace.prepareForExit()
                            NSApp.terminate(nil)
                        }
                    }
                    #endif
                }
        }
        .defaultSize(width: 1080, height: 700)
        .commands {
            CommandMenu("合成预览") {
                Text("隔离资料库 · 静音输入 · 不加载模型")
                Button("显示合成字幕") {
                    workspace.requestedTab = "live"
                    workspace.openSubtitles()
                }
                Button("复习标记") { workspace.openReviewMarks() }
                Button("设置") { PreviewSuite(workspace).showSettings() }
            }
        }
        Settings { SettingsView(workspace: workspace) }
    }
}

@MainActor enum PreviewInput {
    static var timer: Timer?
    static func feed(_ pump: CapturePump, clock: Double) {
        timer?.invalidate()
        // A hardware tap supplies a continuous sample clock. Timer scheduling
        // jitter must not introduce artificial holes in the capture mapping.
        var sampleTime = ProcessInfo.processInfo.systemUptime
        timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak pump] _ in
            guard let pump else { return }
            let format = AVAudioFormat(standardFormatWithSampleRate: 16000, channels: 1)!
            let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 4000)!
            buffer.frameLength = 4000
            buffer.floatChannelData![0].initialize(repeating: 0, count: 4000)
            pump.submit(buffer, hostTime: sampleTime)
            sampleTime += 0.25
        }
    }
}

@MainActor final class PreviewSuite {
    let workspace: Workspace
    let root: URL
    init(_ workspace: Workspace) {
        self.workspace = workspace
        root = URL(fileURLWithPath: Bundle.main.object(forInfoDictionaryKey: "PreviewRoot") as! String)
    }
    func settle(_ seconds: Double = 0.5) async {
        try? await Task.sleep(for: .seconds(seconds))
    }
    var main: NSWindow { NSApp.windows.first(where: Workspace.isMainWindow)! }
    func attribute(_ element: NSObject, _ name: String) -> Any? {
        let selector = NSSelectorFromString(name)
        guard element.responds(to: selector) else { return nil }
        return element.perform(selector)?.takeUnretainedValue()
    }
    func elements(_ root: Any) -> [NSObject] {
        guard let element = root as? NSObject else { return [] }
        let children = attribute(element, "accessibilityChildren") as? [Any]
            ?? (element as? NSView)?.subviews ?? []
        return [element] + children.flatMap { elements($0) }
    }
    @discardableResult func press(_ title: String, in window: NSWindow? = nil) -> Bool {
        #if THEME_PREVIEW
        let anchor = ["返回最新": "caption-latest"].first { $0.key == title }?.value ?? title
        if PreviewAnchors.views[anchor] != nil { return clickAnchor(anchor, window: window) }
        #endif
        let target = elements((window ?? main).contentView!).first { element in
            ["accessibilityLabel", "accessibilityIdentifier", "accessibilityTitle"].contains { name in
                attribute(element, name) as? String == title
            }
        }
        let selector = NSSelectorFromString("accessibilityPerformPress")
        var result = false
        if let target, target.responds(to: selector) {
            typealias Press = @convention(c) (AnyObject, Selector) -> Bool
            let call = unsafeBitCast(target.method(for: selector), to: Press.self)
            result = call(target, selector)
        }
        print("accessibility press \(title): \(result)"); fflush(stdout)
        return result
    }
    func snapshot(_ name: String, window: NSWindow? = nil) async throws {
        await settle()
        let window = window ?? main
        window.orderFrontRegardless()
        await settle(0.2)
        window.displayIfNeeded()
        if ProcessInfo.processInfo.environment["LF_PREVIEW_CAPTURE_BRIDGE"] == "1" {
            let target = root.appendingPathComponent("evidence/" + name + ".png")
            let done = root.appendingPathComponent("capture-done.json")
            try? FileManager.default.removeItem(at: done)
            let request: [String: Any] = ["window": window.windowNumber, "output": target.path]
            try JSONSerialization.data(withJSONObject: request).write(to: root.appendingPathComponent("capture-request.json"), options: .atomic)
            for _ in 0..<200 {
                if FileManager.default.fileExists(atPath: done.path) {
                    let result = try JSONSerialization.jsonObject(with: Data(contentsOf: done)) as! [String: Any]
                    guard result["ok"] as? Bool == true else { throw BackendError.unavailable("Host window capture failed") }
                    if result["captured"] as? Bool == false, let view = window.contentView,
                       let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) {
                        view.cacheDisplay(in: view.bounds, to: bitmap)
                        if let png = bitmap.representation(using: .png, properties: [:]) {
                            try png.write(to: root.appendingPathComponent("evidence/" + name + ".render.png"))
                        }
                    }
                    print(result["captured"] as? Bool == false ? "NOT CAPTURED: " + name : "snapshot: " + name)
                    fflush(stdout); return
                }
                await settle(0.1)
            }
            throw BackendError.unavailable("Host window capture timed out")
        }
        var captured = false
        for _ in 0..<3 {
            let capture = Process()
            capture.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
            capture.arguments = ["-x", "-o", "-l", String(window.windowNumber), root.appendingPathComponent("evidence/" + name + ".png").path]
            try capture.run(); capture.waitUntilExit()
            if capture.terminationStatus == 0 { captured = true; break }
            await settle(0.3)
        }
        guard captured else {
            let ownWindows = (CGWindowListCopyWindowInfo(.optionAll, kCGNullWindowID) as? [[String: Any]] ?? [])
                .filter { ($0[kCGWindowOwnerPID as String] as? Int) == Int(ProcessInfo.processInfo.processIdentifier) }
                .map { ["number": $0[kCGWindowNumber as String] ?? 0,
                        "onscreen": $0[kCGWindowIsOnscreen as String] ?? false,
                        "bounds": $0[kCGWindowBounds as String] ?? ""] }
            print("OWN WINDOWS: \(ownWindows)"); fflush(stdout)
            throw BackendError.unavailable("Window screenshot failed: \(window.windowNumber), visible=\(window.isVisible), frame=\(window.frame)")
        }
        print("snapshot: " + name); fflush(stdout)
    }
    func click(_ x: Double, _ y: Double, in target: NSWindow? = nil) {
        let window = target ?? main
        let point = NSPoint(x: x, y: window.contentView!.bounds.height - y)
        for type: NSEvent.EventType in [.leftMouseDown, .leftMouseUp] {
            let event = NSEvent.mouseEvent(with: type, location: point, modifierFlags: [],
                timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: window.windowNumber,
                context: nil, eventNumber: 1, clickCount: 1, pressure: 1)!
            NSApp.sendEvent(event)
        }
    }
    func showSettings() {
        let window = NSWindow(contentRect: NSRect(x: 100, y: 80, width: 620, height: 700),
                              styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.title = "合成预览设置"
        window.contentView = NSHostingView(rootView: SettingsView(workspace: workspace))
        window.makeKeyAndOrderFront(nil)
    }
    func seedCaptions(_ lecture: Lecture) throws {
        let process = Process()
        process.executableURL = workspace.backend.paths.python
        process.arguments = ["-c", """
import sys, json
from pathlib import Path
from lectureflow.library import Library
from lectureflow import live_store, priority_review
lib=Library(Path(sys.argv[1]))
capture=json.loads(next(Path(sys.argv[3]).glob('Audio/**/capture.json')).read_text())['capture_id']
live_store.create(lib,sys.argv[2],key=capture)
for i,(en,zh) in enumerate([
('A model learns from examples. We keep the original observations for comparison.', '模型从例子中学习，我们保留原始观察结果以便比较。'),
('When the input changes, check the evidence before drawing a new conclusion. A longer explanation needs enough space to remain readable.', '当输入发生变化时，应先检查证据，再得出新的结论。这是一段用于检验窗口缩小与字号增大时阅读效果的合成中文长字幕。关键的限定条件和最后一句都应该完整保留，不能因为显示区域较小就被省略。')]):
    if i == 0:
        live_store.save(lib,capture,dict(id='preview-0', start=0.5, end=1.25,
            english='A different draft',chinese='',status='temporary',revision=0),english=True,translate=True)
    live_store.save(lib,capture,dict(id='preview-'+str(i),start=0.5+i*0.75,end=0.5+(i+1)*0.75,english=en,chinese=zh,status='stable',revision=1),english=True,translate=True)
priority_review.review(lib,sys.argv[2],capture,'preview-0','prepare',0,Path(sys.argv[1]),
    decode=lambda _:dict(text='A model learns from examples. We keep the original measurements for comparison.',truncated=False))
task=lib.enqueue(sys.argv[2],translate=True)
lib.claim()
snapshot=json.loads(lib.row('versions',task['version_id'])['snapshot'])
for i,item in enumerate(snapshot):
    lib.commit_unit(task['id'],i,{**item,'english':'Synthetic saved result. Read the original evidence before drawing a conclusion.','chinese':'合成课后结果：得出结论前，请先阅读原始证据。此文字用于界面验证，并非模型推理结果。','metadata':{}})
lib.finish(task['id'])
lib.close()
""", workspace.backend.paths.library.path, lecture.id, lecture.path]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.standardError
        try process.run(); process.waitUntilExit()
        if process.terminationStatus != 0 { throw BackendError.unavailable("Fixture seeding failed") }
    }
    #if ISOLATION_PREVIEW
    func checkPriority() async throws {
        let window = NSWindow(contentRect: NSRect(x: 120, y: 120, width: 550, height: 440),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.title = "优先复核 · 合成验证"
        window.contentView = NSHostingView(rootView: PriorityReview(lecture: workspace.lecture!,
            backend: workspace.backend, playback: workspace.playback))
        window.makeKeyAndOrderFront(nil)
        await settle(0.7)
        try await snapshot("16-priority-list", window: window)
        clickAnchor("priority-item-preview-0", window: window)
        await settle(0.6)
        try await snapshot("17-priority-compare", window: window)
        window.setContentSize(NSSize(width: 820, height: 460))
        try await snapshot("17w-priority-wide", window: window)
        window.setContentSize(NSSize(width: 550, height: 440))
        clickAnchor("priority-play", window: window)
        await settle(0.12)
        guard workspace.playback.reviewID == "preview-0" else {
            throw BackendError.unavailable("复核回听未开始")
        }
        await settle(1.5)
        guard workspace.playback.reviewID == nil && workspace.playback.error == nil else {
            throw BackendError.unavailable("复核回听未按片段停止")
        }
        func checkState(_ state: String) async throws {
            await settle(0.45)
            let data = try await workspace.backend.call(["priority-review", "list", workspace.lecture!.id])
            let result = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            let items = result?["items"] as? [[String: Any]]
            guard items?.first?["state"] as? String == state else {
                throw BackendError.unavailable("复核操作未保存为 " + state)
            }
        }
        clickAnchor("priority-accept", window: window)
        try await checkState("accepted")
        try await snapshot("18-priority-accepted", window: window)
        clickAnchor("priority-undo", window: window)
        try await checkState("ready")
        clickAnchor("priority-ignore", window: window)
        try await checkState("ignored")
        clickAnchor("priority-undo", window: window)
        try await checkState("ready")
        window.setContentSize(NSSize(width: 410, height: 320))
        try await snapshot("19-priority-small-light", window: window)
        if let difference = PreviewAnchors.views["priority-differences", default: []].compactMap(\.view).last(where: { $0.window == window }) {
            func findScroll(_ view: NSView) -> NSScrollView? {
                (view as? NSScrollView) ?? view.subviews.lazy.compactMap { findScroll($0) }.first
            }
            guard let scroll = findScroll(window.contentView!) else { throw BackendError.unavailable("Missing compact comparison scroll") }
            let before = scroll.contentView.bounds.origin.y
            let event = CGEvent(scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 1, wheel1: -500, wheel2: 0, wheel3: 0)!
            scroll.scrollWheel(with: NSEvent(cgEvent: event)!)
            await settle()
            let after = scroll.contentView.bounds.origin.y
            guard !difference.visibleRect.isEmpty, after >= before else { throw BackendError.unavailable("Compact comparison differences unreachable") }
            print("PASS: compact A/B scroll event offset=\(before)->\(after); differences visible=\(!difference.visibleRect.isEmpty)")
            try await snapshot("19b-priority-small-differences", window: window)
        }

        print("PASS: priority review playback, accept, ignore and undo retained in SQLite")
        window.close()
    }

    func checkDelayedChinese() async throws {
        let live = workspace.live
        let lecture = workspace.lecture!
        let session = live.captions.last!.session_id!
        var event: [String: Any] = ["type": "caption", "session_id": session, "id": "preview-delayed",
            "start": 3.0, "end": 4.0, "status": "temporary", "revision": 1,
            "english": "The second model uses more memory.", "chinese": ""]
        live.previewEvent(event, session: session, lecture: lecture.id)
        try await snapshot("06a-english-first")
        let count = live.displayCaptions.count
        event["chinese"] = "第二个模型占用更多内存。"
        event["revision"] = 2
        live.previewEvent(event, session: session, lecture: lecture.id)
        guard live.displayCaptions.count == count,
              live.displayCaptions.last?.chinese == event["chinese"] as? String else {
            throw BackendError.unavailable("译文未更新到对应英文段")
        }
        try await snapshot("06b-chinese-in-place")
        await live.loadHistory(lecture: lecture, backend: workspace.backend)
    }
    #endif

    func run() async {
        do {
            await settle(2)
            NSApp.activate(ignoringOtherApps: true)
            main.makeKeyAndOrderFront(nil)
            main.appearance = NSAppearance(named: .aqua)
            main.setContentSize(NSSize(width: 1080, height: 700))
            try await snapshot("01-empty-light")
            await workspace.createCourse("合成课程 · 课堂阅读")
            let course = workspace.course!
            try await snapshot("02-course-light")
            main.setContentSize(NSSize(width: 740, height: 520))
            try await snapshot("02b-course-small")
            guard let id = await workspace.createLecture(course.id, title: "Lecture 01 · 合成演示") else {
                throw BackendError.unavailable("合成 Lecture 创建失败")
            }
            for _ in 0..<30 where workspace.lecture?.id != id || workspace.busy {
                await workspace.refresh()
                await settle(0.2)
            }
            guard workspace.lecture?.id == id, !workspace.busy else {
                throw BackendError.unavailable("合成 Lecture 尚未就绪")
            }
            workspace.configureSession(CapturePreset.recording.flags)
            workspace.primarySessionAction()
            await settle(2)
            guard workspace.session.state == .running && workspace.live.isCapturing else {
                throw BackendError.unavailable(workspace.live.error ?? workspace.notice ?? "未进入采集状态")
            }
            main.setContentSize(NSSize(width: 740, height: 520))
            workspace.notice = "字幕处理中断，正在自动恢复；少量译文可能未完成，可用录音课后转写。"
            try await snapshot("03-recording-small-light")
            #if THEME_PREVIEW
            UserDefaults.standard.set("forest", forKey: LFTheme.preferenceKey)
            try await snapshot("03b-recording-forest-light")
            guard workspace.live.isCapturing && workspace.session.state == .running,
                  workspace.lecture?.id == id else {
                throw BackendError.unavailable("主题切换改变了采集或讲次")
            }
            UserDefaults.standard.set("ice", forKey: LFTheme.preferenceKey)
            print("PASS: theme switching preserves active capture and lecture")
            #endif
            workspace.pauseSession()
            try await snapshot("04-paused-small-light")
            workspace.configureSession(CapturePreset.subtitles.flags)
            try await snapshot("04b-paused-subtitles-empty")
            guard !workspace.session.desired.recording, workspace.session.state == .paused,
                  workspace.lecture?.segments.isEmpty == true else {
                throw BackendError.unavailable("Paused subtitle-only empty-state fixture changed capture or saved audio")
            }
            workspace.configureSession(CapturePreset.recording.flags)
            workspace.primarySessionAction()
            await settle(1)
            workspace.endSession()
            await settle(1)
            await workspace.refresh()
            precondition(workspace.lecture!.segments.count == 1, "Pause/resume must preserve one recording")
            try await snapshot("05-ended-small-light")
            try seedCaptions(workspace.lecture!)
            await workspace.live.loadHistory(lecture: workspace.lecture!, backend: workspace.backend)
            await workspace.refresh()
            await settle()
            workspace.requestedTab = "live"
            main.setContentSize(NSSize(width: 1080, height: 700))
            await settle()
            // Exercise the real source picker after entering the transcript page.
            // requestedTab routes to the existing live source without an extra picker click.
            try await snapshot("06-live-light")
            #if ISOLATION_PREVIEW
            try await checkDelayedChinese()
            try await checkPriority()
            #endif
            main.makeKeyAndOrderFront(nil)
            UserDefaults.standard.set(true, forKey: "previewDark")
            main.appearance = NSAppearance(named: .darkAqua)
            main.setContentSize(NSSize(width: 740, height: 520))
            UserDefaults.standard.set(28.0, forKey: "transcriptTextSize")
            try await snapshot("07-live-small-dark-large")
            if !press("caption-layout-toggle") { click(685, 299) }
            try await snapshot("07b-expanded-small-dark")
            let pageUp = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [],
                timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: main.windowNumber,
                context: nil, characters: "", charactersIgnoringModifiers: "", isARepeat: false, keyCode: 116)!
            NSApp.sendEvent(pageUp)
            await settle()
            try await snapshot("07c-keyboard-reading")
            if !press("返回最新") { click(685, 199) }
            try await snapshot("07d-return-latest")
            NSApp.activate(ignoringOtherApps: true)
            main.makeKeyAndOrderFront(nil)
            #if THEME_PREVIEW
            clickAnchor("caption-search")
            #else
            click(390, 199)
            #endif
            await settle()
            if let editor = main.firstResponder as? NSTextView {
                editor.insertText("unmatched-synthetic-query", replacementRange: NSRange(location: NSNotFound, length: 0))
                try await snapshot("12-search-empty")
                if !press("返回最新") { click(685, 199) }
                try await snapshot("12b-search-cleared")
            } else { print("UNVERIFIED: search field focus") }
            workspace.markCurrentForReview()
            await workspace.reviews.finishWrites()
            precondition(workspace.reviews.activeCount == 1)
            UserDefaults.standard.set("both", forKey: "subtitleLanguage")
            UserDefaults.standard.set(36.0, forKey: "subtitleSize")
            workspace.openSubtitles()
            await settle()
            let floating = NSApp.windows.first { $0.title == "LectureFlow 字幕" }!
            floating.setContentSize(NSSize(width: 420, height: 420))
            try await snapshot("08-floating-long", window: floating)
            floating.setContentSize(NSSize(width: 320, height: 180))
            UserDefaults.standard.set(56.0, forKey: "subtitleSize")
            try await snapshot("09-floating-small-largest", window: floating)
            UserDefaults.standard.set("zh", forKey: "subtitleLanguage")
            UserDefaults.standard.set(28.0, forKey: "subtitleSize")
            floating.setContentSize(NSSize(width: 420, height: 420))
            try await snapshot("09b-floating-chinese", window: floating)
            workspace.hideSubtitles()
            main.setContentSize(NSSize(width: 1080, height: 700))
            workspace.openReviewMarks()
            await settle(1)
            try await snapshot("10-review", window: main.sheets.first ?? main)
            if let audio = workspace.reviews.detail?.audio {
                workspace.playback.playReview(audio, markID: workspace.reviews.selectedID!)
                await settle(4)
                guard workspace.playback.error == nil && workspace.playback.reviewID == nil else {
                    throw BackendError.unavailable(workspace.playback.error ?? "Playback did not stop")
                }
                print("PASS: bounded synthetic bookmark playback")
            } else { throw BackendError.unavailable("Missing synthetic bookmark audio") }
            workspace.reviewPresented = false
            await settle()
            workspace.requestedTab = "transcript"
            await settle()
            try await snapshot("13-offline-dark")
            UserDefaults.standard.set(false, forKey: "previewDark")
            main.appearance = NSAppearance(named: .aqua)
            try await snapshot("14-offline-light")
            workspace.error = "合成测试：操作未完成。已有录音保留，请重试。"
            try await snapshot("15-error", window: main.sheets.first ?? main)
            workspace.error = nil
            await settle()
            showSettings()
            await settle()
            let settings = NSApp.windows.first { $0.title == "合成预览设置" }!
            try await snapshot("11-settings", window: settings)
            settings.close()
            #if THEME_PREVIEW
            try await checkThemes()
            if CommandLine.arguments.contains("--audit") { try await checkUsability() }
            #endif
            print("PASS: native capture, pause/resume, seal, saved history, bookmark and playback")
            fflush(stdout)
            UserDefaults.standard.set(15.0, forKey: "transcriptTextSize")
            UserDefaults.standard.set(22.0, forKey: "subtitleSize")
            await workspace.prepareForExit()
            NSApp.terminate(nil)
        } catch {
            print("FAIL: \(error)"); fflush(stdout)
            await workspace.prepareForExit()
            exit(1)
        }
    }

    #if THEME_PREVIEW
    func checkThemeControls() async {
        do {
            await settle(2)
            NSApp.activate(ignoringOtherApps: true)
            UserDefaults.standard.set("forest", forKey: LFTheme.preferenceKey)
            showSettings()
            await settle()
            let window = NSApp.windows.first { $0.title == "合成预览设置" }!
            window.makeKeyAndOrderFront(nil)
            clickAnchor("settings-theme-reset", window: window)
            await settle()
            guard UserDefaults.standard.string(forKey: LFTheme.preferenceKey) == "ice" else {
                throw BackendError.unavailable("恢复默认配色按钮未生效")
            }
            try await snapshot("28-settings-reset-click", window: window)
            clickAnchor("settings-theme", window: window, xFraction: 0.85)
            await settle()
            guard UserDefaults.standard.string(forKey: LFTheme.preferenceKey) == "forest" else {
                throw BackendError.unavailable("配色选择器未写入墨绿主题")
            }
            try await snapshot("29-settings-picker-selection", window: window)
            print("PASS: real Settings reset button and theme Picker persisted selection")
            UserDefaults.standard.synchronize()
            fflush(stdout)
            window.close()
            await workspace.prepareForExit()
            NSApp.terminate(nil)
        } catch {
            print("FAIL: \(error)"); fflush(stdout)
            await workspace.prepareForExit()
            exit(1)
        }
    }

    func checkThemes() async throws {
        main.setContentSize(NSSize(width: 740, height: 520))
        workspace.requestedTab = "live"
        UserDefaults.standard.set(15.0, forKey: "transcriptTextSize")
        let lectureID = workspace.lecture!.id
        let captionIDs = workspace.live.displayCaptions.map(\.id)
        func scrollViews(_ view: NSView) -> [NSScrollView] {
            (view as? NSScrollView).map { [$0] } ?? view.subviews.flatMap(scrollViews)
        }
        await settle()
        let pageUp = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [],
            timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: main.windowNumber,
            context: nil, characters: "", charactersIgnoringModifiers: "", isARepeat: false, keyCode: 116)!
        main.makeKeyAndOrderFront(nil)
        NSApp.sendEvent(pageUp)
        await settle()
        let readingScroll = scrollViews(main.contentView!).filter {
            $0.bounds.width > 400 && ($0.documentView?.bounds.height ?? 0) > $0.contentView.bounds.height
        }.max { $0.bounds.width < $1.bounds.width }
        if let readingScroll {
            readingScroll.contentView.scroll(to: NSPoint(x: 0, y: min(40,
                readingScroll.documentView!.bounds.height - readingScroll.contentView.bounds.height)))
            readingScroll.reflectScrolledClipView(readingScroll.contentView)
        }
        // Let SwiftUI finish reconciling the native scroll before measuring.
        // Measuring immediately after scroll(to:) observes a transient clip origin.
        await settle()
        let readingOffset = readingScroll?.contentView.bounds.origin.y
        print("reading offset baseline: \(readingOffset ?? -1)")
        for theme in ["ice", "forest"] {
            UserDefaults.standard.set(theme, forKey: LFTheme.preferenceKey)
            try await snapshot("20-\(theme)-light")
            print("reading offset \(theme): \(readingScroll?.contentView.bounds.origin.y ?? -1)")
            if let readingScroll, let readingOffset,
               abs(readingScroll.contentView.bounds.origin.y - readingOffset) > 0.5 {
                throw BackendError.unavailable("配色切换改变了阅读滚动位置")
            }
        }
        print(readingOffset == nil ? "NOTE: no scrollable AppKit reading view exposed; offset not asserted"
              : "PASS: reading scroll offset unchanged across theme switches")
        UserDefaults.standard.set("both", forKey: "subtitleLanguage")
        UserDefaults.standard.set(22.0, forKey: "subtitleSize")
        workspace.openSubtitles()
        await settle()
        let floating = NSApp.windows.first { $0.title == "LectureFlow 字幕" }!
        floating.setContentSize(NSSize(width: 320, height: 240))
        try await snapshot("21-forest-floating", window: floating)
        workspace.hideSubtitles()
        let review = NSWindow(contentRect: NSRect(x: 120, y: 120, width: 550, height: 440),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        review.isReleasedWhenClosed = false
        review.title = "优先复核 · 合成验证"
        review.contentView = NSHostingView(rootView: PriorityReview(lecture: workspace.lecture!,
            backend: workspace.backend, playback: workspace.playback))
        review.makeKeyAndOrderFront(nil)
        await settle(0.7)
        clickAnchor("priority-item-preview-0", window: review)
        await settle()
        review.setContentSize(NSSize(width: 410, height: 320))
        try await snapshot("22-forest-review", window: review)
        review.close()
        showSettings()
        await settle()
        let settings = NSApp.windows.first { $0.title == "合成预览设置" }!
        try await snapshot("23-forest-settings", window: settings)
        settings.close()
        UserDefaults.standard.set(true, forKey: "previewDark")
        main.appearance = NSAppearance(named: .darkAqua)
        try await snapshot("24-forest-existing-dark")
        UserDefaults.standard.set(false, forKey: "previewDark")
        main.appearance = NSAppearance(named: .aqua)
        UserDefaults.standard.set("ice", forKey: LFTheme.preferenceKey)
        try await snapshot("27-ice-restored")
        guard workspace.lecture?.id == lectureID,
              workspace.live.displayCaptions.map(\.id) == captionIDs else {
            throw BackendError.unavailable("Theme switch replaced lecture or caption state")
        }
        print("PASS: theme variants, reset and identity preserved; persistence check follows in new process")
        UserDefaults.standard.set("forest", forKey: LFTheme.preferenceKey)
        UserDefaults.standard.synchronize()
    }
    #endif
}
