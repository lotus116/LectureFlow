import SwiftUI

struct TrashItem: Decodable, Identifiable {
    let id: String
    let kind: String
    let name: String
}
struct TrashList: Decodable { let items: [TrashItem] }

struct TrashView: View {
    @ObservedObject var workspace: Workspace
    @Environment(\.dismiss) private var dismiss
    @State private var items: [TrashItem] = []
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack { Text("废纸篓").font(.title2.bold()); Spacer(); Button("完成") { dismiss() } }
            Text("移入的资料仍保存在本机，可恢复。原始导入文件始终不受影响。")
                .font(.callout).foregroundStyle(.secondary)
            List(items) { item in
                HStack {
                    Label(item.name, systemImage: item.kind == "segment" ? "waveform" : "folder")
                    Spacer()
                    Button("恢复") {
                        Task {
                            await workspace.perform(["library", "restore", item.kind, item.id])
                            await reload()
                        }
                    }
                }
            }
            if items.isEmpty { Text("废纸篓为空").foregroundStyle(.secondary) }
        }.padding(24).frame(width: 600, height: 410).task { await reload() }
    }
    private func reload() async {
        do { items = try JSONDecoder().decode(TrashList.self, from: await workspace.backend.call(["library", "trash"])).items }
        catch { workspace.error = error.localizedDescription }
    }
}
