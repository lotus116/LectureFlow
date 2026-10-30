import CoreGraphics
import CryptoKit
import Foundation
import PDFKit
import Vision

public struct DocumentPage: Codable, Sendable {
    public let page: Int
    public let text: String
    public let method: String
}
public struct DocumentExtraction: Codable, Sendable {
    public let sha256: String
    public let pages: [DocumentPage]
}

public enum DocumentExtractor {
    public static func pdf(_ source: URL, forceOCR: Bool = false) throws -> DocumentExtraction {
        let handle = try FileHandle(forReadingFrom: source)
        defer { try? handle.close() }
        var digest = SHA256()
        while let data = try handle.read(upToCount: 1_048_576), !data.isEmpty { digest.update(data: data) }
        let hash = digest.finalize().map { String(format: "%02x", $0) }.joined()
        guard let document = PDFDocument(url: source), !document.isLocked else {
            throw CocoaError(.fileReadCorruptFile)
        }
        guard document.pageCount <= 1000 else { throw CocoaError(.fileReadTooLarge) }
        var pages: [DocumentPage] = []
        var characters = 0
        for index in 0..<document.pageCount {
            let item: DocumentPage = try autoreleasepool {
                guard let page = document.page(at: index) else { throw CocoaError(.fileReadCorruptFile) }
                let existing = page.string ?? ""
                if !forceOCR && existing.trimmingCharacters(in: .whitespacesAndNewlines).count >= 30 {
                    return DocumentPage(page: index + 1, text: existing, method: "text")
                }
                let bounds = page.bounds(for: .mediaBox)
                guard bounds.width > 0, bounds.height > 0 else { throw CocoaError(.fileReadCorruptFile) }
                let scale = min(3, 2000 / max(bounds.width, bounds.height))
                let width = max(1, Int(bounds.width * scale)), height = max(1, Int(bounds.height * scale))
                guard let context = CGContext(data: nil, width: width, height: height,
                    bitsPerComponent: 8, bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(),
                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
                    throw CocoaError(.fileReadTooLarge)
                }
                context.setFillColor(CGColor(gray: 1, alpha: 1))
                context.fill(CGRect(x: 0, y: 0, width: width, height: height))
                context.scaleBy(x: scale, y: scale)
                context.translateBy(x: -bounds.minX, y: -bounds.minY)
                page.draw(with: .mediaBox, to: context)
                guard let image = context.makeImage() else { throw CocoaError(.fileReadCorruptFile) }
                let request = VNRecognizeTextRequest()
                request.recognitionLevel = .accurate
                request.recognitionLanguages = ["en-US", "zh-Hans"]
                request.usesLanguageCorrection = true
                try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
                let recognized = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
                    .joined(separator: "\n")
                return DocumentPage(page: index + 1, text: recognized.isEmpty ? existing : recognized, method: "ocr")
            }
            characters += item.text.count
            guard characters <= 2_000_000 else { throw CocoaError(.fileReadTooLarge) }
            pages.append(item)
        }
        return DocumentExtraction(sha256: hash, pages: pages)
    }
}
