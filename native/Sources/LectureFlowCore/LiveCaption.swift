import Foundation

public struct LiveCaption: Decodable, Identifiable {
    public let id: String
    public let start: Double
    public let end: Double
    public let english: String
    public let chinese: String
    public let status: String
    public let revision: Int
    public let session_id: String?

    public init(id: String, start: Double, end: Double, english: String, chinese: String,
                status: String, revision: Int, sessionID: String?) {
        self.id = id; self.start = start; self.end = end
        self.english = english; self.chinese = chinese; self.status = status
        self.revision = revision; self.session_id = sessionID
    }

    /// Derived display only. Stored fragments and their source intervals remain independent.
    public static func paragraphs(_ fragments: [LiveCaption]) -> [LiveCaption] {
        var result: [LiveCaption] = []
        var count = 0
        for fragment in fragments {
            if let previous = result.last, count < 3,
               previous.session_id != nil, previous.session_id == fragment.session_id,
               previous.status != "temporary", fragment.status != "temporary",
               fragment.start >= previous.end - 0.1, fragment.start - previous.end <= 0.35,
               fragment.end - previous.start <= 12, previous.end - previous.start <= 6,
               !(previous.english.isEmpty ? previous.chinese : previous.english)
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                    .hasSuffixSentenceEnd {
                result[result.count - 1] = LiveCaption(
                    id: previous.id, start: previous.start, end: fragment.end,
                    english: [previous.english, fragment.english].filter { !$0.isEmpty }.joined(separator: " "),
                    chinese: [previous.chinese, fragment.chinese].filter { !$0.isEmpty }.joined(separator: " "),
                    status: fragment.status, revision: previous.revision + fragment.revision,
                    sessionID: fragment.session_id)
                count += 1
            } else {
                result.append(fragment); count = 1
            }
        }
        return result
    }
}

private extension String {
    var hasSuffixSentenceEnd: Bool {
        guard let last else { return false }
        return ".!?。！？".contains(last)
    }
}
