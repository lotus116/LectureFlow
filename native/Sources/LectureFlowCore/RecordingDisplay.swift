import Foundation

/// Display-only naming. Imported filenames and recovery warnings retain their identity.
public struct RecordingDisplay {
    public let title: String
    public let recordedAt: Date?

    public init(originalName: String, position: Int) {
        let prefix = "录制 · "
        let iso = ISO8601DateFormatter()
        var date: Date?
        if originalName.hasPrefix(prefix) {
            let stamp = String(originalName.dropFirst(prefix.count))
            date = iso.date(from: stamp)
            if date == nil {
                iso.formatOptions.insert(.withFractionalSeconds)
                date = iso.date(from: stamp)
            }
        }
        recordedAt = date
        title = date == nil ? originalName : String(format: "录音 %02d", position + 1)
    }

    public func subtitle(duration: String, locale: Locale = .current, timeZone: TimeZone = .current) -> String {
        guard let recordedAt else { return duration }
        let formatter = DateFormatter()
        formatter.locale = locale
        formatter.timeZone = timeZone
        formatter.setLocalizedDateFormatFromTemplate("MMMdHHmm")
        return formatter.string(from: recordedAt) + " · " + duration
    }
}
