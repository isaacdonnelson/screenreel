import CoreGraphics
import CoreImage
import CoreVideo
import Foundation

struct Ripple {
    var x: Double
    var y: Double
    var age: Double
}

func mapSourcePoint(_ point: CGPoint, crop: CGRect, dest: CGRect) -> CGPoint {
    let u = (point.x - crop.minX) / max(crop.width, 0.001)
    let v = (point.y - crop.minY) / max(crop.height, 0.001)
    return CGPoint(x: dest.minX + u * dest.width, y: dest.minY + v * dest.height)
}

func mapSourceRect(_ rect: CGRect, crop: CGRect, dest: CGRect) -> CGRect {
    let scaleX = dest.width / max(crop.width, 0.001)
    let scaleY = dest.height / max(crop.height, 0.001)
    return CGRect(
        x: dest.minX + (rect.minX - crop.minX) * scaleX,
        y: dest.minY + (rect.minY - crop.minY) * scaleY,
        width: rect.width * scaleX,
        height: rect.height * scaleY
    )
}

func makeSolidImage(width: Int, height: Int, top: (CGFloat, CGFloat, CGFloat), bottom: (CGFloat, CGFloat, CGFloat)) -> CGImage {
    var bytes = [UInt8](repeating: 0, count: width * height * 4)
    for y in 0..<height {
        let color = y < height / 2 ? top : bottom
        for x in 0..<width {
            let index = (y * width + x) * 4
            bytes[index] = UInt8(color.0 * 255)
            bytes[index + 1] = UInt8(color.1 * 255)
            bytes[index + 2] = UInt8(color.2 * 255)
            bytes[index + 3] = 255
        }
    }
    let data = Data(bytes) as CFData
    let provider = CGDataProvider(data: data)!
    return CGImage(
        width: width,
        height: height,
        bitsPerComponent: 8,
        bitsPerPixel: 32,
        bytesPerRow: width * 4,
        space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue),
        provider: provider,
        decode: nil,
        shouldInterpolate: false,
        intent: .defaultIntent
    )!
}

func makeCheckerImage(width: Int, height: Int) -> CGImage {
    var bytes = [UInt8](repeating: 255, count: width * height * 4)
    for y in 0..<height {
        for x in 0..<width {
            let index = (y * width + x) * 4
            if y < 50 && x < 100 {
                if x % 2 == 0 {
                    bytes[index] = 255
                    bytes[index + 1] = 0
                    bytes[index + 2] = 0
                } else {
                    bytes[index] = 0
                    bytes[index + 1] = 255
                    bytes[index + 2] = 0
                }
            } else if y >= height - 50 && x < 100 {
                bytes[index] = 0
                bytes[index + 1] = 0
                bytes[index + 2] = 255
            }
            bytes[index + 3] = 255
        }
    }
    let data = Data(bytes) as CFData
    let provider = CGDataProvider(data: data)!
    return CGImage(
        width: width,
        height: height,
        bitsPerComponent: 8,
        bitsPerPixel: 32,
        bytesPerRow: width * 4,
        space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue),
        provider: provider,
        decode: nil,
        shouldInterpolate: false,
        intent: .defaultIntent
    )!
}

func pixel(of image: CGImage, x: Int, y: Int) -> (UInt8, UInt8, UInt8, UInt8) {
    let width = image.width
    let height = image.height
    var bytes = [UInt8](repeating: 0, count: width * height * 4)
    let colorSpace = CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB()
    let context = CGContext(
        data: &bytes,
        width: width,
        height: height,
        bitsPerComponent: 8,
        bytesPerRow: width * 4,
        space: colorSpace,
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    )!
    context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
    let row = height - 1 - min(max(0, y), height - 1)
    let index = (row * width + min(max(0, x), width - 1)) * 4
    return (bytes[index], bytes[index + 1], bytes[index + 2], bytes[index + 3])
}

func renderFrame(
    source: CGImage,
    canvas: Canvas,
    style: Style,
    cursor: CGPoint?,
    focus: CGPoint,
    zoomScale: Double,
    ripples: [Ripple],
    blurs: [BlurRect],
    covers: [CoverRect] = [],
    context: CIContext
) throws -> CGImage {
    let width = canvas.width
    let height = canvas.height
    var bytes = [UInt8](repeating: 0, count: width * height * 4)
    let colorSpace = CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB()
    guard let ctx = CGContext(
        data: &bytes,
        width: width,
        height: height,
        bitsPerComponent: 8,
        bytesPerRow: width * 4,
        space: colorSpace,
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    ) else {
        throw ToolError("Could not create a frame")
    }
    ctx.translateBy(x: 0, y: CGFloat(height))
    ctx.scaleBy(x: 1, y: -1)
    ctx.interpolationQuality = .high
    ctx.setAllowsAntialiasing(true)
    let source = applySourceBlurs(source, blurs: blurs, context: context)

    let color = try parseColor(style.background)
    ctx.setFillColor(CGColor(srgbRed: color.0, green: color.1, blue: color.2, alpha: 1))
    ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))

    let sourceSize = CGSize(width: source.width, height: source.height)
    let canvasSize = CGSize(width: width, height: height)
    let layout = frameLayout(
        sourceSize: sourceSize,
        canvasSize: canvasSize,
        padding: style.padding,
        focus: focus,
        zoomScale: zoomScale
    )
    let radius = min(CGFloat(style.radius), min(layout.dest.width, layout.dest.height) / 2)
    let window = CGPath(roundedRect: layout.dest, cornerWidth: radius, cornerHeight: radius, transform: nil)

    ctx.saveGState()
    ctx.setShadow(
        offset: CGSize(width: 0, height: 12),
        blur: CGFloat(max(0, style.shadow)),
        color: CGColor(srgbRed: 0, green: 0, blue: 0, alpha: 0.45)
    )
    ctx.addPath(window)
    ctx.setFillColor(CGColor(srgbRed: 0, green: 0, blue: 0, alpha: 1))
    ctx.fillPath()
    ctx.restoreGState()

    ctx.saveGState()
    ctx.addPath(window)
    ctx.clip()
    drawImage(source, crop: layout.crop, dest: layout.dest, in: ctx)
    drawCovers(covers, source: source, crop: layout.crop, dest: layout.dest, in: ctx)
    for ripple in ripples where ripple.age >= 0 && ripple.age <= 0.45 {
        let point = mapSourcePoint(CGPoint(x: ripple.x, y: ripple.y), crop: layout.crop, dest: layout.dest)
        let grow = CGFloat(min(1, ripple.age / 0.45))
        let ring = 8 + 64 * grow
        ctx.setStrokeColor(CGColor(srgbRed: 1, green: 1, blue: 1, alpha: (1 - grow) * 0.75))
        ctx.setLineWidth(3)
        ctx.strokeEllipse(in: CGRect(x: point.x - ring, y: point.y - ring, width: ring * 2, height: ring * 2))
    }
    ctx.restoreGState()

    if let cursor {
        let point = mapSourcePoint(cursor, crop: layout.crop, dest: layout.dest)
        let visible = layout.dest.insetBy(dx: -48, dy: -48).contains(point)
        if visible {
            drawCursor(at: point, scale: CGFloat(max(0.4, style.cursorScale)), in: ctx)
        }
    }

    guard let image = ctx.makeImage() else {
        throw ToolError("Could not read a rendered frame")
    }
    return image
}

func copyImage(_ image: CGImage, into buffer: CVPixelBuffer) {
    CVPixelBufferLockBaseAddress(buffer, [])
    defer { CVPixelBufferUnlockBaseAddress(buffer, []) }
    let width = CVPixelBufferGetWidth(buffer)
    let height = CVPixelBufferGetHeight(buffer)
    guard let ctx = CGContext(
        data: CVPixelBufferGetBaseAddress(buffer),
        width: width,
        height: height,
        bitsPerComponent: 8,
        bytesPerRow: CVPixelBufferGetBytesPerRow(buffer),
        space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGBitmapInfo.byteOrder32Little.rawValue | CGImageAlphaInfo.premultipliedFirst.rawValue
    ) else { return }
    ctx.interpolationQuality = .high
    ctx.translateBy(x: 0, y: CGFloat(height))
    ctx.scaleBy(x: 1, y: -1)
    ctx.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
}

private func drawImage(_ image: CGImage, crop: CGRect, dest: CGRect, in ctx: CGContext) {
    let bounds = CGRect(x: 0, y: 0, width: image.width, height: image.height)
    let integral = crop.integral.intersection(bounds)
    guard integral.width >= 1, integral.height >= 1, let cropped = image.cropping(to: integral) else { return }
    ctx.draw(cropped, in: dest)
}

/// Paint blurs into source pixels before the camera crop. Rects are top-left, y down.
func applySourceBlurs(_ source: CGImage, blurs: [BlurRect], context: CIContext) -> CGImage {
    if blurs.isEmpty { return source }
    let width = source.width
    let height = source.height
    var bytes = [UInt8](repeating: 0, count: width * height * 4)
    let colorSpace = CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB()
    guard let ctx = CGContext(
        data: &bytes,
        width: width,
        height: height,
        bitsPerComponent: 8,
        bytesPerRow: width * 4,
        space: colorSpace,
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    ) else { return source }
    ctx.translateBy(x: 0, y: CGFloat(height))
    ctx.scaleBy(x: 1, y: -1)
    ctx.interpolationQuality = .high
    ctx.draw(source, in: CGRect(x: 0, y: 0, width: width, height: height))
    let bounds = CGRect(x: 0, y: 0, width: width, height: height)
    for blur in blurs {
        let rect = CGRect(x: blur.x, y: blur.y, width: blur.width, height: blur.height)
        let visible = rect.intersection(bounds).integral
        guard visible.width >= 2, visible.height >= 2, let patch = source.cropping(to: visible) else { continue }
        let input = CIImage(cgImage: patch)
        let blurred = input
            .clampedToExtent()
            .applyingFilter("CIGaussianBlur", parameters: [kCIInputRadiusKey: blur.radius])
            .cropped(to: input.extent)
        guard let output = context.createCGImage(blurred, from: blurred.extent) else { continue }
        ctx.draw(output, in: visible)
    }
    return ctx.makeImage() ?? source
}

private func drawCovers(_ covers: [CoverRect], source: CGImage, crop: CGRect, dest: CGRect, in ctx: CGContext) {
    for cover in covers {
        let rect = CGRect(x: cover.x, y: cover.y, width: cover.width, height: cover.height)
        let mapped = mapSourceRect(rect, crop: crop, dest: dest)
        guard mapped.width > 0, mapped.height > 0 else { continue }
        let color: (CGFloat, CGFloat, CGFloat)
        if let hex = cover.color, let parsed = try? parseColor(hex) {
            color = parsed
        } else {
            let sampleX = min(max(0, Int(rect.minX)), max(0, source.width - 1))
            let sampleY = min(max(0, Int(rect.minY) - 2), max(0, source.height - 1))
            let sample = pixel(of: source, x: sampleX, y: sampleY)
            color = (CGFloat(sample.0) / 255, CGFloat(sample.1) / 255, CGFloat(sample.2) / 255)
        }
        ctx.setFillColor(CGColor(srgbRed: color.0, green: color.1, blue: color.2, alpha: 1))
        ctx.fill(mapped)
    }
}

private func drawCursor(at tip: CGPoint, scale: CGFloat, in ctx: CGContext) {
    let points = [
        CGPoint(x: 0, y: 0),
        CGPoint(x: 0, y: 19),
        CGPoint(x: 4.7, y: 14.5),
        CGPoint(x: 7.8, y: 21.4),
        CGPoint(x: 10.6, y: 20.2),
        CGPoint(x: 7.4, y: 13.4),
        CGPoint(x: 13.4, y: 13.2),
    ]
    let path = CGMutablePath()
    path.move(to: CGPoint(x: tip.x + points[0].x * scale, y: tip.y + points[0].y * scale))
    for point in points.dropFirst() {
        path.addLine(to: CGPoint(x: tip.x + point.x * scale, y: tip.y + point.y * scale))
    }
    path.closeSubpath()
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: 1), blur: 2, color: CGColor(srgbRed: 0, green: 0, blue: 0, alpha: 0.4))
    ctx.addPath(path)
    ctx.setFillColor(CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 1))
    ctx.fillPath()
    ctx.addPath(path)
    ctx.setStrokeColor(CGColor(srgbRed: 0, green: 0, blue: 0, alpha: 0.9))
    ctx.setLineWidth(max(1, scale * 0.7))
    ctx.setLineJoin(.round)
    ctx.strokePath()
    ctx.restoreGState()
}
