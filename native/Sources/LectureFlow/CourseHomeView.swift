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
        ZStack(alignment: .topLeading) {
            LFChrome.iceMist()
                .frame(height: 120)
                .allowsHitTesting(false)

            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .firstTextBaseline, spacing: LFChrome.space16) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(course.name)
                            .font(.system(size: LFChrome.titleSize, weight: .semibold))
                            .lineLimit(2)
                            .help(course.name)
                        Text(metaLine)
                            .font(.system(size: LFChrome.captionSize))
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: LFChrome.space8)
                    Button {
                        onOpenMaterials()
                    } label: {
                        Label("课程资料", systemImage: "doc.richtext")
                    }
                    .buttonStyle(.borderless)
                    .foregroundStyle(LFChrome.accentInk)
                    .help(termCount == 0 ? "管理课件与辅助术语" : "\(documentCount) 份资料 · \(termCount) 个术语")
                    .accessibilityLabel("课程资料，\(documentCount) 份")
                }

                HStack(spacing: LFChrome.space8) {
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
                    .tint(LFChrome.accent)
                    .controlSize(.large)
                    .accessibilityHint("创建 Lecture 并开始采集")

                    Menu {
                        Button("新建空 Lecture", action: onCreateLecture)
                        Button("导入已有录音", action: onImportAudio)
                    } label: {
                        Image(systemName: "ellipsis.circle")
                            .font(.system(size: 16, weight: .medium))
                            .foregroundStyle(.secondary)
                            .frame(width: 28, height: 28)
                            .contentShape(Rectangle())
                    }
                    .menuStyle(.borderlessButton)
                    .help("新建空记录或导入录音")
                    .accessibilityLabel("更多")

                    Spacer(minLength: 0)
                }
                .padding(.top, LFChrome.space16)
                .padding(.bottom, LFChrome.space20)

                if course.lectures.isEmpty {
                    EmptyPanel(
                        title: "还没有 Lecture",
                        systemImage: "waveform",
                        description: "开始新 Lecture 会创建并启动采集。也可以先建空记录，或导入已有录音。",
                        actionTitle: "开始新 Lecture",
                        action: onStartNew,
                        useAppIcon: true
                    )
                } else {
                    HStack(alignment: .firstTextBaseline) {
                        LFSectionLabel(text: "Lecture")
                        Spacer(minLength: 0)
                    }
                    .padding(.bottom, LFChrome.space8)

                    Rectangle()
                        .fill(LFChrome.hairline)
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
                                        .fill(LFChrome.hairline)
                                        .frame(height: 1)
                                        .padding(.leading, 48)
                                }
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                }
            }
            .lfContentPadding()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(LFChrome.canvas)
        .task(id: course.id) { await loadCounts() }
        .onChange(of: workspace.overview.courses.map(\.id)) { _, _ in Task { await loadCounts() } }
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
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .background(
                RoundedRectangle(cornerRadius: LFChrome.radiusS, style: .continuous)
                    .fill(configuration.isPressed ? LFChrome.rowHover : Color.clear)
            )
            .animation(LFChrome.motion, value: configuration.isPressed)
    }
}
