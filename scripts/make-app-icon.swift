import AppKit
import ImageIO
import UniformTypeIdentifiers

// Packaging only: preserve the source artwork and apply a continuous alpha mask.
// The fourth-power corner joins the straight edges with continuous curvature.
let arguments = CommandLine.arguments
precondition(arguments.count == 3, "Usage: make-app-icon.swift source.png output.iconset")
let sourceURL = URL(fileURLWithPath: arguments[1])
let output = URL(fileURLWithPath: arguments[2], isDirectory: true)
guard let source = CGImageSourceCreateWithURL(sourceURL as CFURL, nil),
      let original = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
    fatalError("Cannot read original artwork")
}
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
let colorSpace = CGColorSpace(name: CGColorSpace.sRGB)!
for logical in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let size = logical * scale
        let stride = size * 4
        var rgba = [UInt8](repeating: 0, count: size * stride)
        rgba.withUnsafeMutableBytes { bytes in
            let context = CGContext(data: bytes.baseAddress, width: size, height: size,
                                    bitsPerComponent: 8, bytesPerRow: stride, space: colorSpace,
                                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
            context.interpolationQuality = .high
            context.draw(original, in: CGRect(x: 0, y: 0, width: size, height: size))
        }
        let inset = max(0.5, Double(size) * 0.015)
        let radius = (Double(size) - 2 * inset) * 0.22
        let innerMin = inset + radius
        let innerMax = Double(size) - inset - radius
        // Supersampled coverage changes alpha only; RGB of the resized original is retained.
        for y in 0..<size {
            for x in 0..<size {
                var coverage = 0
                for sy in 0..<4 {
                    for sx in 0..<4 {
                        let px = Double(x) + (Double(sx) + 0.5) / 4
                        let py = Double(y) + (Double(sy) + 0.5) / 4
                        guard px >= inset, py >= inset,
                              px <= Double(size) - inset, py <= Double(size) - inset else { continue }
                        let dx = max(innerMin - px, px - innerMax, 0) / radius
                        let dy = max(innerMin - py, py - innerMax, 0) / radius
                        if pow(dx, 4) + pow(dy, 4) <= 1 { coverage += 1 }
                    }
                }
                rgba[y * stride + x * 4 + 3] = UInt8((coverage * 255 + 8) / 16)
            }
        }
        let provider = CGDataProvider(data: Data(rgba) as CFData)!
        let image = CGImage(width: size, height: size, bitsPerComponent: 8, bitsPerPixel: 32,
                            bytesPerRow: stride, space: colorSpace,
                            bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.last.rawValue),
                            provider: provider, decode: nil, shouldInterpolate: true,
                            intent: .defaultIntent)!
        let suffix = scale == 2 ? "@2x" : ""
        let url = output.appendingPathComponent("icon_\(logical)x\(logical)\(suffix).png")
        let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil)!
        CGImageDestinationAddImage(destination, image, nil)
        precondition(CGImageDestinationFinalize(destination), "Cannot encode icon")
    }
}
