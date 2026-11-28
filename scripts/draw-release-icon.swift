import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

// Original geometric mark for the public release. No fonts or external artwork.
precondition(CommandLine.arguments.count == 2, "Expected path to the output PNG")
let size = 1024
let rgb = CGColorSpace(name: CGColorSpace.sRGB)!
let ctx = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8,
                    bytesPerRow: size * 4, space: rgb,
                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
func color(_ red: CGFloat, _ green: CGFloat, _ blue: CGFloat) -> CGColor {
    CGColor(red: red, green: green, blue: blue, alpha: 1)
}
let gradient = CGGradient(colorsSpace: rgb, colors: [
    color(0.09, 0.28, 0.40), color(0.12, 0.43, 0.58)
] as CFArray, locations: [0, 1])!
ctx.drawLinearGradient(gradient, start: .init(x: 0, y: 0),
                       end: .init(x: size, y: size), options: [])
ctx.setFillColor(color(0.96, 0.99, 1))
let bubble = CGMutablePath()
bubble.addRoundedRect(in: CGRect(x: 191, y: 300, width: 642, height: 429),
                      cornerWidth: 116, cornerHeight: 116)
ctx.addPath(bubble)
ctx.fillPath()
ctx.beginPath()
ctx.move(to: .init(x: 307, y: 357))
ctx.addLine(to: .init(x: 307, y: 233))
ctx.addQuadCurve(to: .init(x: 327, y: 223), control: .init(x: 307, y: 214))
ctx.addLine(to: .init(x: 476, y: 350))
ctx.closePath()
ctx.fillPath()
for (y, width) in [(548.0, 331.0), (428.0, 228.0)] {
    ctx.setFillColor(color(0.12, 0.34, 0.47))
    ctx.addPath(CGPath(roundedRect: CGRect(x: 341, y: y, width: width, height: 44),
                       cornerWidth: 22, cornerHeight: 22, transform: nil))
    ctx.fillPath()
}
let out = URL(fileURLWithPath: CommandLine.arguments[1])
let dst = CGImageDestinationCreateWithURL(out as CFURL, UTType.png.identifier as CFString, 1, nil)!
CGImageDestinationAddImage(dst, ctx.makeImage()!, nil)
precondition(CGImageDestinationFinalize(dst))
