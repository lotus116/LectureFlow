import SwiftUI
import UniformTypeIdentifiers

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
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(course.name)
                        .font(.system(size: 26, weight: .semibold, design: .rounded))
                    Text("\(course.lectures.count) 个 Lecture")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button {
                    onOpenMaterials()
                } label: {
                    Label("课程资料 · \(documentCount) 份", systemImage: "doc.richtext")
                }
                .buttonStyle(.bordered)
                .help(termCount == 0 ? "管理课件与辅助术语" : "\(termCount) 个辅助术语")
            }

            HStack(spacing: 10) {
                Button("开始新 Lecture", systemImage: "record.circle") { onStartNew() }
                    .buttonStyle(.borderedProminent)
                Button("新建 Lecture") { onCreateLecture() }
                Button("导入已有录音") { onImportAudio() }
                Spacer()
            }

            Text("记录方式：\(workspace.session.modeTitle)（开始前可在 Lecture 内更改）")
                .font(.caption)
                .foregroundStyle(.secondary)

            if course.lectures.isEmpty {
                EmptyPanel(
                    title: "还没有 Lecture",
                    systemImage: "waveform",
                    description: "开始新 Lecture 会自动创建并启动采集；新建只建空 Lecture；导入录音不会在取消时留下空记录。"
                )
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List {
                    ForEach(course.lectures) { lecture in
                        Button {
                            workspace.selectCourse(course.id)
                            workspace.selectLecture(lecture.id)
                        } label: {
                            HStack {
                                Label(lecture.title, systemImage: "waveform")
                                Spacer()
                                Text("\(lecture.segments.count) 段")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .buttonStyle(.plain)
                    }
                }
                .listStyle(.inset)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .task(id: course.id) { await loadCounts() }
        .onChange(of: workspace.overview.courses.map(\.id)) { _, _ in Task { await loadCounts() } }
    }

    private func loadCounts() async {
        do {
            let loaded = try JSONDecoder().decode(MaterialList.self, from: await workspace.backend.call(["materials", "list", "course", course.id]))
            documentCount = loaded.documents.count
            termCount = loaded.terms.filter { $0.excluded == 0 }.count
        } catch {
            documentCount = 0
            termCount = 0
        }
    }
}
