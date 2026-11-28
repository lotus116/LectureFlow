import SwiftUI

struct CourseHomeView<Status: View>: View {
    @ThemePalette private var palette
    @ObservedObject var workspace: Workspace
    let course: Course
    var onOpenMaterials: () -> Void
    var onStartNew: () -> Void
    var onCreateLecture: () -> Void
    var onImportAudio: () -> Void
    @ViewBuilder var status: () -> Status
    @State private var documentCount = 0
    @State private var termCount = 0

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .center, spacing: 16) {
                    courseTitle
                    Spacer(minLength: 12)
                    startButton
                }
                VStack(alignment: .leading, spacing: 12) { courseTitle; startButton }
            }
            .padding(16)
            status().padding(.horizontal, 16).padding(.bottom, 8)
            Divider()
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 16) { recordingMode; Spacer(minLength: 8); courseActions }
                VStack(alignment: .leading, spacing: 8) { recordingMode; courseActions }
            }
            .padding(.horizontal, 16).padding(.vertical, 12)
            Divider()
            if course.lectures.isEmpty {
                VStack(alignment: .leading, spacing: 12) {
                    Label("还没有 Lecture", systemImage: "waveform")
                        .font(.system(size: 17, weight: .semibold))
                    Text("从上方开始新 Lecture，录音与转写会保存在这里。")
                        .font(.system(size: 14)).foregroundStyle(palette.secondaryText)
                    Spacer(minLength: 0)
                }.padding(24).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            } else {
                lectureList.padding(.horizontal, 16).padding(.top, 16)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(palette.canvas).foregroundStyle(palette.bodyText)
        .task(id: course.id) { await loadCounts() }
        .onChange(of: workspace.overview.courses.map(\.id)) { _, _ in Task { await loadCounts() } }
    }

    private var courseTitle: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(course.name).font(.system(size: 20, weight: .semibold)).lineLimit(2).help(course.name)
            Text(metaLine).font(.caption).foregroundStyle(palette.secondaryText)
        }
    }

    private var courseActions: some View {
        HStack(spacing: 12) {
            Button("新建空 Lecture", action: onCreateLecture)
            Button("导入已有录音", action: onImportAudio)
            Button(action: onOpenMaterials) { Label("课程资料", systemImage: "doc.richtext") }
                .help(termCount == 0 ? "管理课件与辅助术语" : "\(documentCount) 份资料 · \(termCount) 个术语")
        }.buttonStyle(.borderless).foregroundStyle(palette.accentInk)
    }

    private var recordingMode: some View {
        Text(workspace.session.modeTitle)
            .font(.system(size: LFChrome.captionSize))
            .foregroundStyle(palette.secondaryText)
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityLabel("记录方式：\(workspace.session.modeTitle)")
    }

    private var startButton: some View {
        Button {
            onStartNew()
        } label: {
            Label {
                Text("开始新 Lecture")
                    .font(.system(size: LFChrome.bodySize, weight: .semibold))
            } icon: {
                Image(systemName: "record.circle")
                    .symbolRenderingMode(.monochrome)
            }
        }
        .buttonStyle(.borderedProminent)
        .tint(palette.accent)
        .controlSize(.large)
        .accessibilityHint("创建 Lecture 并开始采集")
        .accessibilityIdentifier("course-start")
    }

    private var lectureList: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                LFSectionLabel(text: "Lecture")
                Spacer(minLength: 0)
            }
            .padding(.bottom, LFChrome.space8)

            Rectangle()
                .fill(palette.hairline)
                .frame(height: 1)

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(course.lectures.enumerated()), id: \.element.id) { index, lecture in
                        Button {
                            workspace.selectCourse(course.id)
                            workspace.selectLecture(lecture.id)
                        } label: {
                            LFListRow(
                                title: lecture.title,
                                subtitle: "\(lecture.segments.count) 段录音"
                            ) {
                                LFIconWell(systemImage: "waveform")
                            } trailing: {
                                Image(systemName: "chevron.right")
                                    .font(.caption.weight(.semibold))
                                    .foregroundStyle(.tertiary)
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(LFQuietRowButtonStyle())

                        if index < course.lectures.count - 1 {
                            Rectangle()
                                .fill(palette.hairline)
                                .frame(height: 1)
                                .padding(.leading, 48)
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        }
    }

    private var metaLine: String {
        var parts = ["\(course.lectures.count) 个 Lecture"]
        if documentCount > 0 { parts.append("\(documentCount) 份资料") }
        if termCount > 0 { parts.append("\(termCount) 个术语") }
        return parts.joined(separator: " · ")
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

private struct LFQuietRowButtonStyle: ButtonStyle {
    @ThemePalette private var palette
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .background(
                RoundedRectangle(cornerRadius: LFChrome.radiusS, style: .continuous)
                    .fill(configuration.isPressed ? palette.rowHover : Color.clear)
            )
            .animation(reduceMotion ? nil : LFChrome.motion, value: configuration.isPressed)
    }
}
