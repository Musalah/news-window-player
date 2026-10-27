// Reads the text in snaps with Apple Vision (Arabic + English), far better than tesseract here.
// Usage: snapocr OUT_DIR DEADLINE_EPOCH < snaps.jsonl
//   in:  one {"id", "v" (is video), "m" (media URL)} per line
//   out: one {"id", "frames": [{"path", "w", "h", "lines": [{"t", "box": [x, y, w, h]}]}]} per line,
//        or {"id", "error"}; frames are saved as PNGs in OUT_DIR, boxes are pixels from the top-left.
// Videos are sampled at 10/35/60/85% of their length. Snaps not started by DEADLINE are skipped.
import AVFoundation
import AppKit
import Vision

let outDir = CommandLine.arguments[1]
let deadline = Double(CommandLine.arguments[2]) ?? .infinity
let videoPoints = [0.1, 0.35, 0.6, 0.85]

func download(_ url: URL) -> Data? {
    var result: Data?
    let done = DispatchSemaphore(value: 0)
    var req = URLRequest(url: url, timeoutInterval: 60)
    req.setValue("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)", forHTTPHeaderField: "User-Agent")
    URLSession.shared.dataTask(with: req) { data, res, _ in
        if (res as? HTTPURLResponse)?.statusCode == 200 { result = data }
        done.signal()
    }.resume()
    done.wait()
    return result
}

func frames(_ snap: [String: Any]) -> [CGImage] {
    guard let s = snap["m"] as? String, let url = URL(string: s) else { return [] }
    if snap["v"] as? Bool != true {
        guard let data = download(url), let img = NSImage(data: data),
              let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return [] }
        return [cg]
    }
    let asset = AVURLAsset(url: url)
    let gen = AVAssetImageGenerator(asset: asset)
    gen.appliesPreferredTrackTransform = true
    let tolerance = CMTime(seconds: 0.3, preferredTimescale: 600)
    gen.requestedTimeToleranceBefore = tolerance
    gen.requestedTimeToleranceAfter = tolerance
    let duration = asset.duration.seconds
    guard duration.isFinite, duration > 0 else { return [] }
    return videoPoints.compactMap { try? gen.copyCGImage(at: CMTime(seconds: duration * $0, preferredTimescale: 600), actualTime: nil) }
}

func read(_ cg: CGImage) -> [[String: Any]] {
    let (w, h) = (Double(cg.width), Double(cg.height))
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["ar-SA", "en-US"]
    req.usesLanguageCorrection = true
    try? VNImageRequestHandler(cgImage: cg).perform([req])
    return (req.results ?? []).compactMap { o -> [String: Any]? in
        guard let best = o.topCandidates(1).first else { return nil }
        let b = o.boundingBox // normalised, origin bottom-left
        return ["t": best.string, "box": [b.minX * w, (1 - b.maxY) * h, b.width * w, b.height * h].map { $0.rounded() }]
    }.sorted { ($0["box"] as! [Double])[1] < ($1["box"] as! [Double])[1] }
}

func save(_ cg: CGImage, _ path: String) -> Bool {
    guard let png = NSBitmapImageRep(cgImage: cg).representation(using: .png, properties: [:]) else { return false }
    return (try? png.write(to: URL(fileURLWithPath: path))) != nil
}

var n = 0
while let line = readLine() {
    guard let snap = (try? JSONSerialization.jsonObject(with: Data(line.utf8))) as? [String: Any],
          let id = snap["id"] as? String else { continue }
    var out: [String: Any] = ["id": id]
    if Date().timeIntervalSince1970 > deadline {
        out["error"] = "out of time"
    } else {
        let imgs = frames(snap)
        if imgs.isEmpty { out["error"] = "no frames" }
        out["frames"] = imgs.compactMap { cg -> [String: Any]? in
            n += 1
            let path = "\(outDir)/\(ProcessInfo.processInfo.processIdentifier)-\(n).png"
            guard save(cg, path) else { return nil }
            return ["path": path, "w": cg.width, "h": cg.height, "lines": read(cg)]
        }
    }
    print(String(data: try! JSONSerialization.data(withJSONObject: out), encoding: .utf8)!)
    fflush(stdout)
}
