import AppKit
import SwiftUI
import UniformTypeIdentifiers

@main
struct LectureFlowApp: App {
    @StateObject private var workspace = Workspace()
    var body: some Scene {
        WindowGroup {
            LibraryView(model: workspace)
                .frame(minWidth: 950, minHeight: 650)
        }
        .defaultSize(width: 1180, height: 790)
    }
}

struct LibraryView: View {
    @ObservedObject var model: Workspace
    @State private var importing = false
    @State private var form: String?
    @State private var courseID = ""
    @State private var newName = ""
    @State private var tab = "audio"
    @State private var cancelling: WorkTask?
    @State private var search = ""

    var body: some View {
        NavigationSplitView {
            List(selection: $model.selectedLecture) {
                ForEach(model.overview.courses) { course in
                    Section {
                        ForEach(course.lectures) { lecture in
                            Label(lecture.title, systemImage: "waveform")
                                .tag(lecture.id)
                        }
                        Button {
                            courseID = course.id; newName = ""; form = "lecture"
                        } label: { Label("新建课堂", systemImage: "plus") }
                        .buttonStyle(.plain).foregroundStyle(.secondary)
                    } header: {
                        Label(course.name, systemImage: "folder")
                    }
                }
            }
            .navigationTitle("LectureFlow")
            .navigationSplitViewColumnWidth(min: 230, ideal: 260)
            .safeAreaInset(edge: .bottom) {
                Button {
                    newName = ""; form = "course"
                } label: {
                    Label("新建课程", systemImage: "folder.badge.plus")
                        .frame(maxWidth: .infinity, alignment: .leading)
                }.buttonStyle(.plain).padding()
            }
        } detail: {
            if let lecture = model.lecture {
                VStack(alignment: .leading, spacing: 18) {
                    header(lecture)
                    HStack(spacing: 8) {
                        Image(systemName: "desktopcomputer")
                        Text("本地处理 · 当前为离线原型，识别与翻译结果需要核对")
                    }.font(.callout).foregroundStyle(.secondary)
                    if let task = model.activeTask { taskView(task) }
                    Picker("内容", selection: $tab) {
                        Text("录音片段").tag("audio")
                        Text("双语转写").tag("transcript")
                    }.pickerStyle(.segmented).frame(width: 260)
                    if tab == "audio" { audioList(lecture) } else { transcriptView }
                }.padding(26)
            } else {
                ContentUnavailableView(
                    "把每堂课放在一起", systemImage: "folder.badge.plus",
                    description: Text("新建课程与课堂，再导入已有录音。")
                )
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
                Text(form == "course" ? "新建课程" : "新建课堂").font(.title2.bold())
                TextField("名称", text: $newName).textFieldStyle(.roundedBorder)
                    .onSubmit { submitForm() }
                HStack {
                    Spacer()
                    Button("取消") { form = nil }.keyboardShortcut(.cancelAction)
                    Button("创建") { submitForm() }
                        .keyboardShortcut(.defaultAction)
                        .disabled(newName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }.padding(24).frame(width: 370)
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
        .onChange(of: model.selectedLecture) { _, _ in model.selectedVersion = nil }
        .task {
            while !Task.isCancelled {
                await model.refresh()
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }

    private func submitForm() {
        let name = newName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return }
        let kind = form
        form = nil
        Task {
            if kind == "course" { await model.createCourse(name) }
            else { await model.createLecture(courseID, title: name) }
        }
    }

    private func header(_ lecture: Lecture) -> some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 7) {
                Text(lecture.title).font(.largeTitle.bold())
                Text("\(lecture.segments.count) 段录音 · \(duration(lecture.segments.reduce(0) { $0 + $1.duration }))")
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button { NSWorkspace.shared.open(URL(fileURLWithPath: lecture.path)) } label: {
                Image(systemName: "folder")
            }.help("在 Finder 中打开课堂")
            Button { importing = true } label: {
                Label("导入录音", systemImage: "plus")
            }.disabled(model.busy)
        }
    }

    private func taskView(_ task: WorkTask) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Image(systemName: task.state == "completed" ? "checkmark.circle" : "waveform")
                Text(task.label).fontWeight(.medium)
                Text(task.scopeLabel).font(.caption).foregroundStyle(.secondary)
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
            ProgressView(value: task.fraction)
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
        }.padding(16).background(.quaternary.opacity(0.45), in: RoundedRectangle(cornerRadius: 12))
    }

    private func audioList(_ lecture: Lecture) -> some View {
        VStack(alignment: .leading, spacing: 15) {
            HStack {
                Text("按下列顺序处理，可拖动调整").font(.callout).foregroundStyle(.secondary)
                Spacer()
                Picker("识别", selection: $model.model) {
                    Text("轻快").tag("asr-small")
                    Text("高精度").tag("asr-large")
                }.frame(width: 160)
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
                            Image(systemName: "waveform").font(.title2).foregroundStyle(.teal)
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
                        }.padding(16)
                            .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 10))
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
        }
    }

    private var transcriptView: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("时间戳以约 30 秒处理区间显示").font(.caption).foregroundStyle(.secondary)
                Spacer()
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
                                if !unit.chinese.isEmpty { Text(unit.chinese).font(.title3).lineSpacing(5) }
                                Text(unit.english).foregroundStyle(.secondary).lineSpacing(4)
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
