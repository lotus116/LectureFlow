import AppKit
import SwiftUI
import UniformTypeIdentifiers

@main
struct LectureFlowApp: App {
    @StateObject private var workspace = Workspace()
    @NSApplicationDelegateAdaptor(LectureFlowDelegate.self) private var delegate
    var body: some Scene {
        WindowGroup {
            LibraryView(model: workspace)
                .frame(minWidth: 920, minHeight: 610)
                .onAppear {
                    delegate.workspace = workspace
                    workspace.revealMain = { [weak appDelegate = delegate] in appDelegate?.showMainWindow() }
                }
        }
        .defaultSize(width: 1120, height: 740)
        .commands {
            CommandMenu("课堂") {
                Button("显示悬浮字幕") { workspace.openSubtitles() }
                    .keyboardShortcut("l", modifiers: [.command, .option])
                Button("仅显示悬浮字幕") { workspace.openSubtitles(hideMain: true) }
                Button("开始／暂停课堂") { workspace.primarySessionAction() }
                    .keyboardShortcut("r", modifiers: [.command, .shift])
                Button("保存并结束课堂") { workspace.endSession() }.disabled(!workspace.session.hasSession)
                Button("暂停全部采集") { workspace.pauseSession() }
                    .keyboardShortcut("p", modifiers: [.command, .shift])
            }
        }
        Settings { SettingsView(workspace: workspace) }
        MenuBarExtra("LectureFlow", systemImage: "captions.bubble") {
            Text(workspace.lecture?.title ?? "LectureFlow")
            Button("打开主窗口") { delegate.showMainWindow() }
            Button("显示悬浮字幕") { workspace.openSubtitles() }
            Button("仅显示悬浮字幕") { workspace.openSubtitles(hideMain: true) }
            Divider()
            Button(workspace.session.primaryTitle) { workspace.primarySessionAction() }.disabled(workspace.lecture == nil)
            Button("保存并结束课堂") { workspace.endSession() }.disabled(!workspace.session.hasSession)
            Button("退出 LectureFlow") { NSApp.terminate(nil) }
        }
    }
}

@MainActor
final class LectureFlowDelegate: NSObject, NSApplicationDelegate {
    weak var workspace: Workspace?
    private var reopened: NSWindowController?
    func applicationWillTerminate(_ notification: Notification) {
        workspace?.live.shutdown()
        workspace?.backend.downloadLifetime.stop(shutdown: true)
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        showMainWindow()
        return true
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func showMainWindow() {
        guard let workspace else { return }
        NSApp.activate(ignoringOtherApps: true)
        if let window = NSApp.windows.first(where: { $0.identifier?.rawValue == "LectureFlowMain" }) {
            window.deminiaturize(nil); window.makeKeyAndOrderFront(nil); return
        }
        if let window = reopened?.window { window.makeKeyAndOrderFront(nil); return }
        let window = NSWindow(contentRect: NSRect(x: 150, y: 120, width: 1180, height: 790),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable],
                              backing: .buffered, defer: false)
        window.title = "LectureFlow"; window.isReleasedWhenClosed = false
        window.contentMinSize = NSSize(width: 920, height: 610)
        window.contentView = NSHostingView(rootView: LibraryView(model: workspace))
        reopened = NSWindowController(window: window)
        window.makeKeyAndOrderFront(nil)
    }
}

private struct ArchiveTarget {
    let kind: String
    let id: String
    let name: String
}

struct LibraryView: View {
    @ObservedObject var model: Workspace
    @State private var importing = false
    @AppStorage("librarySidebarVisible") private var sidebarVisible = true
    @State private var form: String?
    @State private var courseID = ""
    @State private var newName = ""
    @State private var tab = "audio"
    @State private var cancelling: WorkTask?
    @State private var search = ""
    @State private var recordID = ""
    @State private var archiving: ArchiveTarget?
    @State private var showTrash = false
    @State private var showQueue = false
    @State private var sharedCourse: Course?
    @State private var textLanguage = "both"
    @StateObject private var playback = AudioPlayback()
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        LibrarySplitLayout(sidebarVisible: $sidebarVisible) {
            List(selection: Binding(get: { model.selectedLecture }, set: { model.selectLecture($0) })) {
                ForEach(model.overview.courses) { course in
                    Section {
                        ForEach(course.lectures) { lecture in
                            Label(lecture.title, systemImage: "waveform")
                                .tag(lecture.id)
                                .draggable("lectureflow-lecture:" + lecture.id)
                                .dropDestination(for: String.self) { items, _ in
                                    guard let item = items.first, item.hasPrefix("lectureflow-segment:") else { return false }
                                    let id = String(item.dropFirst("lectureflow-segment:".count))
                                    let copy = NSEvent.modifierFlags.contains(.option)
                                    Task { await model.perform(["library", "transfer", id, lecture.id] + (copy ? ["--copy"] : [])) }
                                    return true
                                }
                                .contextMenu {
                                    Button("重命名") { recordID = lecture.id; newName = lecture.title; form = "rename-lecture" }
                                    Menu("移动到课程") {
                                        ForEach(model.overview.courses.filter { $0.id != course.id }) { target in
                                            Button(target.name) { Task { await model.perform(["library", "move-lecture", lecture.id, target.id]) } }
                                        }
                                    }
                                    Divider()
                                    Button("移入废纸篓", role: .destructive) { archiving = ArchiveTarget(kind: "lecture", id: lecture.id, name: lecture.title) }
                                }
                        }
                        Button {
                            courseID = course.id; newName = ""; form = "lecture"
                        } label: { Label("新建课堂", systemImage: "plus") }
                        .buttonStyle(.plain).foregroundStyle(.secondary)
                    } header: {
                        Label(course.name, systemImage: "folder")
                            .draggable("lectureflow-course:" + course.id)
                            .dropDestination(for: String.self) { items, _ in
                                guard let item = items.first else { return false }
                                if item.hasPrefix("lectureflow-lecture:") {
                                    let id = String(item.dropFirst("lectureflow-lecture:".count))
                                    Task { await model.perform(["library", "move-lecture", id, course.id]) }; return true
                                }
                                guard item.hasPrefix("lectureflow-course:") else { return false }
                                let id = String(item.dropFirst("lectureflow-course:".count))
                                var order = model.overview.courses.map(\.id)
                                guard id != course.id, let from = order.firstIndex(of: id) else { return false }
                                order.remove(at: from)
                                if let target = order.firstIndex(of: course.id) { order.insert(id, at: target) }
                                Task { await model.perform(["library", "order-courses"] + order) }; return true
                            }
                            .contextMenu {
                                Button("课程共享课件") { sharedCourse = course }
                                Button("重命名") { recordID = course.id; newName = course.name; form = "rename-course" }
                                Divider()
                                Button("移入废纸篓", role: .destructive) { archiving = ArchiveTarget(kind: "course", id: course.id, name: course.name) }
                            }
                    }
                }
            }
            .listStyle(.sidebar)
            .safeAreaInset(edge: .top) {
                HStack(spacing: 9) {
                    Image(nsImage: NSApp.applicationIconImage).resizable().frame(width: 32, height: 32)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("LectureFlow").font(.headline)
                        Text("你的本地课堂空间").font(.caption2).foregroundStyle(.secondary)
                    }
                    Spacer()
                }.padding(.horizontal, 16).padding(.vertical, 10)
            }
            .safeAreaInset(edge: .bottom) {
                HStack {
                    Button { newName = ""; form = "course" } label: { Label("新建课程", systemImage: "folder.badge.plus") }
                    Spacer()
                    Button { showTrash = true } label: { Image(systemName: "trash") }.help("废纸篓")
                    Button { openSettings() } label: { Image(systemName: "gearshape") }.help("模型、麦克风与总结设置")
                }.buttonStyle(.plain).padding(12)
            }
        } detail: {
            if let lecture = model.lecture {
                VStack(alignment: .leading, spacing: 12) {
                    header(lecture)
                    modelDownloadBanner
                    LiveControlView(workspace: model, live: model.live, lecture: lecture)
                    Picker("内容", selection: $tab) {
                        Text("录音").tag("audio")
                        Text("转写").tag("transcript")
                        Text("实时").tag("live")
                        Text("知识库").tag("materials")
                        Text("笔记").tag("summary")
                    }.pickerStyle(.segmented).labelsHidden()
                    if ["audio", "transcript", "summary"].contains(tab), let task = model.activeTask { taskView(task) }
                    if tab == "audio" { audioList(lecture) }
                    else if tab == "live" {
                        LiveTranscriptView(live: model.live, lecture: lecture, backend: model.backend)
                    } else if tab == "materials" {
                        KnowledgeCenterView(workspace: model, lecture: lecture)
                    } else if tab == "summary" {
                        SummaryView(workspace: model, lecture: lecture).id(lecture.id)
                    } else { transcriptView }
                }.padding(18).id(lecture.id)
                    .dropDestination(for: URL.self) { urls, _ in
                        let audio = urls.filter { ["m4a", "mp3", "wav", "flac"].contains($0.pathExtension.lowercased()) }
                        guard !audio.isEmpty, !model.busy else { return false }
                        Task { await model.importAudio(audio) }; return true
                    }
            } else {
                VStack(spacing: 22) {
                    Image(systemName: "waveform.circle").font(.system(size: 60)).foregroundStyle(.teal)
                    Text("每一堂课，都有自己的空间").font(.largeTitle.bold())
                    Text("1. 新建课程和课堂\n2. 导入录音，或开启课堂录音与字幕\n3. 加入课件，课后整理学习笔记")
                        .lineSpacing(10).foregroundStyle(.secondary)
                    HStack {
                        Button("新建课程") { newName = ""; form = "course" }.buttonStyle(.borderedProminent)
                        Button("模型与资料库设置") { openSettings() }
                    }
                    modelDownloadBanner.frame(maxWidth: 570)
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.audio],
                      allowsMultipleSelection: true) { result in
            switch result {
            case .success(let urls): Task { await model.importAudio(urls) }
            case .failure(let error): model.error = error.localizedDescription
            }
        }
        .sheet(isPresented: Binding(get: { form != nil }, set: { if !$0 { form = nil } })) {
            VStack(alignment: .leading, spacing: 18) {
                Text(form?.hasPrefix("rename") == true ? "重命名" : form == "course" ? "新建课程" : "新建课堂").font(.title2.bold())
                TextField("名称", text: $newName).textFieldStyle(.roundedBorder)
                    .onSubmit { submitForm() }
                HStack {
                    Spacer()
                    Button("取消") { form = nil }.keyboardShortcut(.cancelAction)
                    Button(form?.hasPrefix("rename") == true ? "保存" : "创建") { submitForm() }
                        .keyboardShortcut(.defaultAction)
                        .disabled(newName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }.padding(24).frame(width: 370)
        }
        .sheet(isPresented: $showTrash) { TrashView(workspace: model) }
        .sheet(isPresented: $showQueue) { BackgroundQueueView(workspace: model) }
        .sheet(item: $sharedCourse) { course in
            VStack {
                HStack { Text(course.name).font(.headline); Spacer(); Button("完成") { sharedCourse = nil } }
                MaterialsView(workspace: model, kind: "course", ownerID: course.id)
            }.padding(18).frame(width: 920, height: 660)
        }
        .confirmationDialog("将“\(archiving?.name ?? "")”移入废纸篓？可在应用中恢复。", isPresented: Binding(get: { archiving != nil }, set: { if !$0 { archiving = nil } })) {
            Button("移入废纸篓", role: .destructive) {
                if let target = archiving { playback.stop(); Task { await model.perform(["library", "archive", target.kind, target.id, "--confirmed"]) } }
                archiving = nil
            }
        }
        .alert("操作未完成", isPresented: Binding(
            get: { model.error != nil }, set: { if !$0 { model.error = nil } }
        )) {
            Button("知道了") { model.error = nil }
        } message: { Text(model.error ?? "") }
        .confirmationDialog("取消这次处理？已完成的内容会保留。", isPresented: Binding(
            get: { cancelling != nil }, set: { if !$0 { cancelling = nil } }
        )) {
            Button("取消处理", role: .destructive) {
                if let task = cancelling {
                    Task { await model.perform(["library", "task", task.id, "cancel"]) }
                }
                cancelling = nil
            }
        }
        .onChange(of: model.selectedLecture) { _, next in
            playback.stop()
        }
        .onChange(of: model.requestedTab) { _, value in
            if let value { tab = value; model.requestedTab = nil }
        }
        .onChange(of: model.live.error) { _, error in
            if let error { model.error = error; model.live.error = nil }
        }
        .task { model.startMonitoring() }
        .toolbar(removing: .sidebarToggle)
        .toolbar {
            ToolbarItem(placement: .navigation) {
                Button {
                    withAnimation(.easeInOut(duration: 0.18)) { sidebarVisible.toggle() }
                } label: { Image(systemName: "sidebar.left") }
                    .help("显示或隐藏侧边栏").accessibilityIdentifier("sidebar-toggle")
            }
            ToolbarItem(placement: .primaryAction) {
                Button { showQueue = true } label: {
                    Label("后台 \(model.backgroundItems.count)", systemImage: "list.bullet.rectangle")
                }.help("查看、暂停和继续所有课程的后台任务")
            }
        }
        .safeAreaInset(edge: .bottom) {
            if let message = model.refreshError {
                HStack {
                    Label("资料库暂未刷新：" + message, systemImage: "exclamationmark.circle").lineLimit(1)
                    Spacer()
                    Button("重试") { Task { await model.refresh() } }
                }.font(.caption).padding(8).background(.bar)
            }
        }
        .background(MainWindowMarker())
        .controlSize(.small)
        .tint(Color(red: 0.13, green: 0.45, blue: 0.65))
    }

    private func submitForm() {
        let name = newName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return }
        let kind = form
        form = nil
        Task {
            if kind == "course" { await model.createCourse(name) }
            else if kind == "lecture" { await model.createLecture(courseID, title: name) }
            else { await model.perform(["library", "rename", kind == "rename-course" ? "course" : "lecture", recordID, name]) }
        }
    }

    @ViewBuilder private var modelDownloadBanner: some View {
        if model.installedModels.isEmpty && !model.downloading {
            HStack { ProgressView().controlSize(.small); Text("正在检查本地模型…").font(.callout) }
        } else if model.downloading {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    ProgressView().controlSize(.small)
                    Text(model.downloadMessage).font(.callout)
                    Spacer()
                    Button("暂停下载") { model.cancelModelDownload() }
                }
                if let fraction = model.downloadFraction { ProgressView(value: fraction) }
                Text("可先新建课程、导入资料或保存录音；模型就绪后即可识别与翻译。")
                    .font(.caption).foregroundStyle(.secondary)
            }.padding(12).background(.blue.opacity(0.07), in: RoundedRectangle(cornerRadius: 10))
        } else if !model.hasModel("asr-small") || !model.hasModel("translate") {
            HStack {
                Text("本地模型尚未就绪").font(.callout).foregroundStyle(.secondary)
                Spacer()
                Button("继续下载") {
                    Task { await model.installModels(["asr-small", "translate"].filter { !model.hasModel($0) }) }
                }
            }
        }
    }

    private func header(_ lecture: Lecture) -> some View {
        HStack(alignment: .center, spacing: 10) {
            VStack(alignment: .leading, spacing: 4) {
                Text(lecture.title).font(.system(size: 24, weight: .semibold)).lineLimit(1)
                Text("\(lecture.segments.count) 段录音 · \(duration(lecture.segments.reduce(0) { $0 + $1.duration }))")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Button("悬浮字幕", systemImage: "pip") { model.openSubtitles() }
            Menu {
                Button("仅显示字幕") { model.openSubtitles(hideMain: true) }
                Button("在 Finder 中打开") { NSWorkspace.shared.open(URL(fileURLWithPath: lecture.path)) }
                Button("恢复意外中断的录音") { Task { await model.perform(["library", "recover-recordings", lecture.id]) } }
            } label: { Image(systemName: "ellipsis") }.menuStyle(.borderlessButton).frame(width: 22)
            Button { importing = true } label: {
                Label("导入录音", systemImage: "plus")
            }.disabled(model.busy)
        }
    }

    private func taskView(_ task: WorkTask) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Image(systemName: task.state == "completed" ? "checkmark.circle" : "waveform")
                Text(task.label).fontWeight(.medium)
                Text(task.scopeLabel).font(.caption).foregroundStyle(.secondary)
                if let count = task.material_count, count > 0 {
                    Text("参考课件 \(count) 份").font(.caption).foregroundStyle(.secondary)
                }
                if task.stale != 0 { Text("素材已变动，需要更新").foregroundStyle(.orange) }
                Spacer()
                Text("\(task.next_unit) / \(task.total_units)").monospacedDigit().foregroundStyle(.secondary)
                if ["running", "queued"].contains(task.state) {
                    Button("暂停") {
                        Task { await model.perform(["library", "task", task.id, "pause"]) }
                    }
                }
                if task.state == "paused" || task.state == "failed" {
                    Button(task.state == "paused" ? "继续" : "重试") {
                        Task { await model.perform([
                            "library", "task", task.id, task.state == "paused" ? "resume" : "retry"
                        ]) }
                    }
                }
                if ["running", "queued", "paused", "failed"].contains(task.state) {
                    Button("取消") { cancelling = task }
                }
                Button("导出") { Task { await model.export() } }.disabled(task.next_unit == 0)
            }
            if task.state != "completed" { ProgressView(value: task.fraction).controlSize(.mini) }
            if let error = task.error { Text(error).font(.caption).foregroundStyle(.red) }
            if let tasks = model.lecture?.tasks, tasks.count > 1 {
                Picker("转写版本", selection: Binding(
                    get: { model.selectedVersion ?? tasks[0].version_id },
                    set: { model.selectedVersion = $0; Task { await model.refresh() } }
                )) {
                    ForEach(Array(tasks.enumerated()), id: \.element.id) { index, item in
                        Text("版本 \(tasks.count - index) · \(item.scopeLabel) · \(item.modelLabel) · \(item.label)")
                            .tag(item.version_id)
                    }
                }.frame(maxWidth: 330)
            }
        }.font(.callout).padding(10).background(.quaternary.opacity(0.25), in: RoundedRectangle(cornerRadius: 7))
    }

    private func audioList(_ lecture: Lecture) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("按下列顺序处理，可拖动调整").font(.callout).foregroundStyle(.secondary)
                Spacer()
                Toggle("中文翻译", isOn: $model.translate).toggleStyle(.checkbox)
                Button("转写整堂课") {
                    tab = "transcript"
                    Task { await model.enqueue() }
                }.buttonStyle(.borderedProminent)
                    .disabled(model.busy || lecture.segments.isEmpty)
            }
            ScrollView {
                LazyVStack(spacing: 8) {
                    ForEach(Array(lecture.segments.enumerated()), id: \.element.id) { index, segment in
                        HStack(spacing: 14) {
                            Image(systemName: "line.3.horizontal").foregroundStyle(.tertiary)
                            Text(String(format: "%02d", index + 1)).monospacedDigit().foregroundStyle(.secondary)
                            Button { playback.play(segment) } label: {
                                Image(systemName: playback.segmentID == segment.id ? "stop.circle.fill" : "play.circle")
                                    .font(.title2).foregroundStyle(.teal)
                            }.buttonStyle(.plain).help("播放／停止录音")
                            VStack(alignment: .leading, spacing: 5) {
                                Text(segment.original_name).fontWeight(.medium)
                                Text(duration(segment.duration)).font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button("单段转写") {
                                Task {
                                    await model.perform([
                                        "library", "enqueue", lecture.id, "--asr", model.model,
                                        "--segment", segment.id
                                    ] + (model.translate ? [] : ["--no-translation"]))
                                }
                            }.disabled(model.busy)
                            Menu {
                                Menu("移动到课堂") { transferTargets(segment, copy: false) }
                                Menu("复制到课堂") { transferTargets(segment, copy: true) }
                                Button("导出录音") {
                                    Task { do { try await playback.export(segment) } catch { model.error = error.localizedDescription } }
                                }
                                Divider()
                                Button("移入废纸篓", role: .destructive) { archiving = ArchiveTarget(kind: "segment", id: segment.id, name: segment.original_name) }
                            } label: { Image(systemName: "ellipsis") }.menuStyle(.borderlessButton).frame(width: 25)
                        }.padding(10)
                            .background(.quaternary.opacity(0.25), in: RoundedRectangle(cornerRadius: 7))
                            .draggable("lectureflow-segment:" + segment.id)
                            .dropDestination(for: String.self) { items, _ in
                                guard let item = items.first,
                                      item.hasPrefix("lectureflow-segment:") else { return false }
                                let dragged = String(item.dropFirst("lectureflow-segment:".count))
                                Task { await model.move(dragged, before: segment.id) }
                                return true
                            }
                    }
                }
            }
            .overlay {
                if lecture.segments.isEmpty {
                    ContentUnavailableView {
                        Label("还没有录音", systemImage: "waveform")
                    } description: {
                        Text("点击上方开始录音，或导入已有课堂录音。多段文件导入后可拖动排序。")
                    } actions: {
                        Button("导入已有录音") { importing = true }
                    }
                }
            }
        }
    }

    private var transcriptView: some View {
        VStack(alignment: .leading, spacing: 12) {
            if model.transcript?.possible_context_bias == true {
                HStack {
                    Text("此版本使用过旧版课件识别提示。如出现词表混入，可从原音重新转写，旧版本会保留。")
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button("重新转写") { Task { await model.enqueue() } }.disabled(model.busy)
                }
            }
            if model.transcript?.source_missing == true {
                Label("部分关联录音已永久清空，已完成的转写文字仍保留。", systemImage: "waveform.slash")
                    .font(.caption).foregroundStyle(.secondary)
            }
            HStack {
                Text("时间戳以约 30 秒处理区间显示").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Picker("显示", selection: $textLanguage) {
                    Text("双语").tag("both"); Text("中文").tag("zh"); Text("英文").tag("en")
                }.frame(width: 110)
                Button("复制全文") {
                    let text = model.transcript?.units.map { unit in
                        [clock(unit.logical_start), textLanguage == "en" ? "" : unit.chinese,
                         textLanguage == "zh" ? "" : unit.english].filter { !$0.isEmpty }.joined(separator: "\n")
                    }.joined(separator: "\n\n") ?? ""
                    NSPasteboard.general.clearContents(); NSPasteboard.general.setString(text, forType: .string)
                }
                TextField("搜索转写", text: $search).textFieldStyle(.roundedBorder).frame(width: 220)
            }
            if let transcript = model.transcript, !transcript.units.isEmpty {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 22) {
                        ForEach(transcript.units.filter {
                            search.isEmpty || $0.english.localizedCaseInsensitiveContains(search)
                                || $0.chinese.localizedCaseInsensitiveContains(search)
                        }) { unit in
                            VStack(alignment: .leading, spacing: 8) {
                                Text(clock(unit.logical_start)).font(.caption.monospaced()).foregroundStyle(.teal)
                                if textLanguage != "en" && !unit.chinese.isEmpty { Text(unit.chinese).font(.title3).lineSpacing(5) }
                                if textLanguage != "zh" { Text(unit.english).foregroundStyle(.secondary).lineSpacing(4) }
                            }.frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
                            Divider()
                        }
                    }.padding(.vertical, 8)
                }
            } else {
                ContentUnavailableView("还没有转写内容", systemImage: "text.bubble",
                                       description: Text("开始处理后，完成的内容会陆续出现在这里。"))
            }
        }
    }

    private func transferTargets(_ segment: AudioSegment, copy: Bool) -> some View {
        ForEach(model.overview.courses) { course in
            Menu(course.name) {
                ForEach(course.lectures.filter { $0.id != model.selectedLecture }) { target in
                    Button(target.title) {
                        playback.stop()
                        Task { await model.perform(["library", "transfer", segment.id, target.id] + (copy ? ["--copy"] : [])) }
                    }
                }
            }
        }
    }

    private func duration(_ seconds: Double) -> String {
        if seconds < 60 { return "\(Int(seconds)) 秒" }
        let minutes = Int(seconds) / 60
        return minutes >= 60 ? "\(minutes / 60) 小时 \(minutes % 60) 分钟" : "\(minutes) 分钟"
    }
    private func clock(_ seconds: Double) -> String {
        let value = Int(seconds)
        return String(format: "%02d:%02d:%02d", value / 3600, value / 60 % 60, value % 60)
    }
}
