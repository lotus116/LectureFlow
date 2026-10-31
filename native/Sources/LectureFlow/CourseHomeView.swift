import SwiftUI

struct CourseHomeView: View {
    @ObservedObject var workspace: Workspace
    let course: Course
    var onOpenMaterials: () -> Void
    var onStartNew: () -> Void
    var onCreateLecture: () -> Void
    var onImportAudio: () -> Void
    @State private var documentCount = 0
    @State private var termCount = 0

    var body: some View {
        VStack(alignment: .leading, spacing: LFChrome.space24) {
            HStack(alignment: .firstTextBaseline, spacing: LFChrome.space16) {
                VStack(alignment: .leading, spacing: LFChrome.space4) {
                    Text(course.name)
                        .font(.system(size: LFChrome.titleSize, weight: .semibold))
                        .lineLimit(2)
                        .help(course.name)
                    Text("\(course.lectures.count) 个 Lecture")
                        .font(.system(size: LFChrome.captionSize))
                        .foregroundStyle(.secondary)
                }
                Spacer(minLength: LFChrome.space8)
                Button {
                    onOpenMaterials()
                } label: {
                    Label("课程资料 · \(documentCount)", systemImage: "doc.richtext")
                }
                .buttonStyle(.bordered)
                .controlSize(.regular)
                .help(termCount == 0 ? "管理课件与辅助术语" : "\(termCount) 个辅助术语")
                .accessibilityLabel("课程资料，\(documentCount) 份")
            }

            HStack(spacing: LFChrome.space8) {
                Button("开始新 Lecture", systemImage: "record.circle") { onStartNew() }
                    .buttonStyle(.borderedProminent)
                    .tint(LFChrome.accent)
                    .controlSize(.large)
                    .accessibilityHint("创建 Lecture 并开始采集")
                Button("新建 Lecture") { onCreateLecture() }
                    .controlSize(.regular)
                Button("导入已有录音") { onImportAudio() }
                    .controlSize(.regular)
                Spacer(minLength: 0)
            }

            Text(workspace.session.modeTitle)
                .font(.system(size: LFChrome.captionSize))
                .foregroundStyle(.tertiary)
                .accessibilityLabel("记录方式：\(workspace.session.modeTitle)")

            if course.lectures.isEmpty {
                EmptyPanel(
                    title: "还没有 Lecture",
                    systemImage: "waveform",
                    description: "开始新 Lecture 会创建并启动采集；新建只建空记录；取消导入不会留下空项。",
                    actionTitle: "开始新 Lecture",
                    action: onStartNew
                )
            } else {
                VStack(alignment: .leading, spacing: LFChrome.space8) {
                    LFSectionLabel(text: "Lecture")
                    List {
                        ForEach(course.lectures) { lecture in
                            Button {
                                workspace.selectCourse(course.id)
                                workspace.selectLecture(lecture.id)
                            } label: {
                                LFListRow(
                                    title: lecture.title,
                                    subtitle: "\(lecture.segments.count) 段录音"
                                ) {
                                    Image(systemName: "waveform")
                                        .foregroundStyle(LFChrome.accent)
                                        .frame(width: 20)
                                } trailing: {
                                    Image(systemName: "chevron.right")
                                        .font(.caption.weight(.semibold))
                                        .foregroundStyle(.tertiary)
                                }
                            }
                            .buttonStyle(.plain)
                            .listRowInsets(EdgeInsets(top: 2, leading: 4, bottom: 2, trailing: 4))
                            .listRowSeparator(.visible, edges: .bottom)
                        }
                    }
                    .listStyle(.plain)
                    .scrollContentBackground(.hidden)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
        }
        .lfContentPadding()
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .task(id: course.id) { await loadCounts() }
        .onChange(of: workspace.overview.courses.map(\.id)) { _, _ in Task { await loadCounts() } }
    }

    private func loadCounts() async {
        do {
            let loaded = try JSONDecoder().decode(
                MaterialList.self,
                from: await workspace.backend.call(["materials", "list", "course", course.id])
            )
            documentCount = loaded.documents.count
            termCount = loaded.terms.filter { $0.excluded == 0 }.count
        } catch {
            documentCount = 0
            termCount = 0
        }
    }
}
