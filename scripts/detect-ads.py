"""Flag ad snaps in stories.json (adds "ad": 1) by reading their text with OCR.

The accounts label ads themselves: trendnews24 writes "Ad" on a red bar, saudi.panther
writes a small "إعلان" in the top corner. A snap counts as an ad only when that word
stands alone on its line, so headlines like "إعلان حرب" stay. Results are cached by snap
id in ads-cache.json so each snap is only scanned once.
Needs: tesseract (eng + ara), ffmpeg/ffprobe, Pillow.
"""
import json, os, re, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor
from PIL import Image

ROOT = os.path.join(os.path.dirname(__file__), "..")
STORIES = os.path.join(ROOT, "stories.json")
CACHE = os.path.join(ROOT, "ads-cache.json")
LABEL = re.compile(r"^\W*(Ad|AD|إعلان|اعلان)\W*$")
VIDEO_POINTS = (0.1, 0.35, 0.6, 0.85)
DEADLINE = time.time() + 6 * 60  # leave the rest for the next 10-minute run
os.environ["OMP_THREAD_LIMIT"] = "1"  # we run tesseracts in parallel; its own threads only fight each other


def run(cmd, timeout=90):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout


def ocr(img, tmp, lang, psm="11"):
    p = os.path.join(tmp, f"ocr-{lang}.png")
    img.save(p)
    return run(["tesseract", p, "-", "-l", lang, "--psm", psm])


def has_label(text):
    return any(LABEL.match(line.strip()) for line in text.splitlines())


def red_band(im):
    """Tallest horizontal band where most of the row is red, or None."""
    w, h = im.size
    px = im.load()
    best, start = (0, 0), None
    for y in range(0, h + 2, 2):
        red = y < h and sum(1 for x in range(0, w, 8)
                            if px[x, y][0] > 150 and px[x, y][1] < 80 and px[x, y][2] < 90) * 8 > w * 0.6
        if red and start is None:
            start = y
        elif not red and start is not None:
            if y - start > best[1] - best[0]:
                best = (start, y)
            start = None
    return best if best[1] - best[0] >= h * 0.03 else None


def white_boxes(im):
    """Small solid near-white boxes with dark letters inside (like a white "Ad" badge), as full-size crop boxes."""
    scale = max(1, im.width // 310)
    g = im.convert("L").resize((im.width // scale, im.height // scale))
    w, h = g.size
    px = g.load()
    seen = bytearray(w * h)
    out = []
    for y0 in range(h):
        for x0 in range(w):
            if seen[y0 * w + x0] or px[x0, y0] < 235:
                continue
            stack, n = [(x0, y0)], 0
            seen[y0 * w + x0] = 1
            x1 = x2 = x0
            y1 = y2 = y0
            while stack:
                x, y = stack.pop()
                n += 1
                x1, x2, y1, y2 = min(x1, x), max(x2, x), min(y1, y), max(y2, y)
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx] and px[nx, ny] >= 235:
                        seen[ny * w + nx] = 1
                        stack.append((nx, ny))
            bw, bh = x2 - x1 + 1, y2 - y1 + 1
            if not (w * 0.04 <= bw <= w * 0.2 and 0.5 <= bw / bh <= 2.5 and n > bw * bh * 0.5):
                continue
            # a badge has a clean white edge and dark letters inside; without this check tesseract
            # sometimes "reads" Ad off a plain bright patch
            edge = [(x, y) for y in range(y1, y2 + 1) for x in range(x1, x2 + 1) if x in (x1, x2) or y in (y1, y2)]
            ink = sum(1 for y in range(y1 + 1, y2) for x in range(x1 + 1, x2) if px[x, y] < 100)
            if sum(px[p] >= 235 for p in edge) >= len(edge) * 0.8 and ink >= (bw - 2) * (bh - 2) * 0.05:
                out.append((x1 * scale, y1 * scale, (x2 + 1) * scale, (y2 + 1) * scale))
    return out


def frame_is_ad(path, tmp):
    im = Image.open(path).convert("RGB")
    band = red_band(im)
    if band:  # white "Ad" on the red bar: upscale and threshold it to black on white
        crop = im.crop((0, band[0], im.width, band[1]))
        crop = crop.resize((crop.width * 2, crop.height * 2)).convert("L").point(lambda v: 0 if v > 200 else 255)
        if has_label(ocr(crop, tmp, "eng")):
            return True
    for box in white_boxes(im):  # "Ad" badge: tesseract only reads it cropped on its own
        if LABEL.match(ocr(im.crop(box), tmp, "eng", psm="7").strip()):
            return True
    g = im.convert("L")
    for x in (im.width * 2 // 3, 0):  # faint grey "إعلان" in a top corner, beside the logo
        top = g.crop((x, 0, x + im.width // 3, im.height * 3 // 10))
        top = top.resize((top.width * 2, top.height * 2)).point(lambda v: 0 if v < 230 else 255)
        if has_label(ocr(top, tmp, "ara")):
            return True
    return has_label(ocr(im, tmp, "eng+ara"))


def frames(snap, tmp):
    if not snap["v"]:
        p = os.path.join(tmp, "img")
        run(["curl", "-sfL", "--max-time", "60", "-o", p, snap["m"]])
        return [p] if os.path.exists(p) else []
    dur = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", snap["m"]]).strip() or 0)
    out = []
    for i, f in enumerate(VIDEO_POINTS if dur else (0,)):
        p = os.path.join(tmp, f"f{i}.png")
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{dur * f:.2f}", "-i", snap["m"], "-frames:v", "1", p], timeout=120)
        if os.path.exists(p):
            out.append(p)
    return out


def scan(snap):
    """1 = ad, 0 = not an ad, None = couldn't fetch or out of time (retry next run)."""
    if time.time() > DEADLINE:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        try:
            paths = frames(snap, tmp)
            if not paths:
                return None
            return int(any(frame_is_ad(p, tmp) for p in paths))
        except Exception as e:
            print("warn:", snap["id"], e, file=sys.stderr)
            return None


def main():
    data = json.load(open(STORIES))
    rescan = "--rescan" in sys.argv  # after changing the rules: ignore cached results
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) and not rescan else {}
    snaps = [s for a in data["accounts"] for s in a["snaps"]]
    todo = [s for s in snaps if s["id"] not in cache and not s.get("ad")]
    with ThreadPoolExecutor(os.cpu_count() or 4) as ex:
        for s, r in zip(todo, ex.map(scan, todo)):
            if r is not None:
                cache[s["id"]] = r
                if r:
                    print("ad:", s["id"], flush=True)
    for s in snaps:
        if s.get("ad") or cache.get(s["id"]):
            s["ad"] = 1
    cache = {s["id"]: cache[s["id"]] for s in snaps if s["id"] in cache}  # drop expired snaps
    for a in data["accounts"]:
        print(f"{a['u']}: {sum(1 for s in a['snaps'] if s.get('ad'))} ads / {len(a['snaps'])}")
    print(f"scanned {sum(1 for s in todo if s['id'] in cache)} of {len(todo)} new snaps in {time.time() - DEADLINE + 360:.0f}s")
    json.dump(data, open(STORIES, "w"), ensure_ascii=False, separators=(",", ":"))
    json.dump(cache, open(CACHE, "w"), separators=(",", ":"))


if __name__ == "__main__":
    main()
