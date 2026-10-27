"""Read every snap's text with Apple Vision (snapocr.swift), then:
- flag ads and promos in stories.json ("ad": 1), which the page can hide
- keep each snap's readable text in ocr.json, for the page's swipe-up text view

What counts as an ad or promo (any frame of the snap):
- label:    a line that is just "Ad" or "إعلان" (trendnews24's red bar or badge, saudi.panther's corner),
            so a headline like "إعلان حرب" doesn't count
- code:     "كود" with a discount code on the same line ("كود: TN", "بكود TN26") or "كود (ال)خصم"
- sticker:  a store's domain on a Snapchat link sticker, i.e. on flat grey (not white, not colour)
- brand:    a domain from another snap that carried a label or sticker ("aloyayri.sa" anywhere)
ocr.json doubles as the cache: a snap already in it isn't read again.
Needs: macOS (Vision, AVFoundation), swiftc, Pillow. Pass --rescan to re-read everything.
"""
import difflib, json, os, re, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor
from PIL import Image

ROOT = os.path.join(os.path.dirname(__file__), "..")
STORIES = os.path.join(ROOT, "stories.json")
OCR = os.path.join(ROOT, "ocr.json")
SWIFT = os.path.join(os.path.dirname(__file__), "snapocr.swift")
DEADLINE = time.time() + 6 * 60  # leave the rest for the next 10-minute run

LABEL = re.compile(r"^\W*(Ad|AD|إعلان|اعلان)\W*$")
CODE_WORD = re.compile(r"(?:^|[\s\W])[وفب]?كود(?!\w)")
CODE = re.compile(r"(?<![A-Za-z0-9])(?=[A-Z0-9]*[A-Z])[A-Z0-9]{2,8}(?![A-Za-z0-9])")
CODE_PHRASE = re.compile(r"كود\s+(?:ال)?خص")  # كود خصم / كود الخصم; OCR often mangles the ending
DOMAIN = re.compile(r"[a-z0-9-]{3,}(?:\.[a-z0-9-]+)*\.(?:sa|com|net|org|online|store|shop|co)\b", re.I)
NOT_PROMO = re.compile(r"gov|trendnews|saudipanther|snapchat", re.I)  # a link to these is just a source
ARABIC = re.compile(r"[؀-ۿ]")
BRAND_LINES = ["ترند الأخبار", "سعودي بانثر", "SAUDI PANTHER", "SAUDI.PANTHER", "TRENDNEWS24"]


def domains(text):
    return {d.lower().removeprefix("www.") for d in DOMAIN.findall(text) if not NOT_PROMO.search(d)}


def on_sticker(img, box):
    """True when the ring just around box is flat neutral grey, like a link sticker (not a white page)."""
    l, t, w, h = map(int, box)
    ring = [img.getpixel((x, y)) for x in range(max(0, l - 6), min(img.width, l + w + 6), 3)
            for y in (max(0, t - 5), min(img.height - 1, t + h + 4))]
    grey = sum(1 for p in ring if max(p) - min(p) <= 10 and 40 <= sum(p) / 3 <= 200)
    return bool(ring) and grey >= len(ring) * 0.7


def is_noise(line, frame_h):
    """Watermarks, the accounts' own names, ad labels and OCR crumbs: not worth copying."""
    t = line["t"].strip()
    if len(t) < 2 or LABEL.match(t):
        return True
    squashed = re.sub(r"\s", "", t).upper()
    if any(difflib.SequenceMatcher(None, squashed, re.sub(r"\s", "", b).upper()).ratio() >= 0.7 for b in BRAND_LINES):
        return True
    # all-caps Latin crumbs are the tiled watermark read badly ("TRENONENS 24", "IFNONBUS24")
    return not ARABIC.search(t) and not re.search(r"[a-z]", t) and not DOMAIN.search(t) and len(t.split()) <= 3


def judge(frames):
    """(why it's an ad or None, domains seen, readable text) for one snap's frames."""
    why, seen, text = None, set(), []
    for f in frames:
        img = Image.open(f["path"]).convert("RGB")
        for line in f["lines"]:
            t = line["t"].strip()
            seen |= domains(t)
            if why:
                pass
            elif LABEL.match(t):
                why = f"label: {t}"
            elif CODE_WORD.search(t) and (CODE.search(t) or CODE_PHRASE.search(t)):
                why = f"code: {t}"
            elif domains(t) and on_sticker(img, line["box"]):
                why = f"sticker: {t}"
            if not is_noise(line, f["h"]) and not any(difflib.SequenceMatcher(None, t, x).ratio() >= 0.85 for x in text):
                text.append(t)  # video frames repeat their captions
    return why, sorted(seen), "\n".join(text)


def read_all(snaps, tmp):
    """Run snapocr on snaps in parallel batches; yields its JSON results."""
    exe = os.path.join(tmp, "snapocr")
    subprocess.run(["swiftc", "-O", "-swift-version", "5", SWIFT, "-o", exe], check=True, capture_output=True)
    workers = max(1, min(os.cpu_count() or 3, 6))
    batches = [snaps[i::workers] for i in range(workers)]

    def run(batch):
        stdin = "".join(json.dumps({"id": s["id"], "v": s["v"], "m": s["m"]}) + "\n" for s in batch)
        out = subprocess.run([exe, tmp, str(DEADLINE)], input=stdin, capture_output=True, text=True, timeout=15 * 60).stdout
        return [json.loads(l) for l in out.splitlines()]

    with ThreadPoolExecutor(workers) as ex:
        for results in ex.map(run, batches):
            yield from results


def main():
    data = json.load(open(STORIES))
    rescan = "--rescan" in sys.argv  # after changing the rules: read everything again
    cache = json.load(open(OCR)) if os.path.exists(OCR) and not rescan else {}
    snaps = [s for a in data["accounts"] for s in a["snaps"]]
    todo = [s for s in snaps if s["id"] not in cache]
    with tempfile.TemporaryDirectory() as tmp:
        for r in read_all(todo, tmp):
            if r.get("error"):
                print("skip:", r["id"], r["error"], file=sys.stderr)
                continue
            why, seen, text = judge(r["frames"])
            cache[r["id"]] = {"t": text, **({"why": why} if why else {}), **({"d": seen} if seen else {})}
    cache = {s["id"]: cache[s["id"]] for s in snaps if s["id"] in cache}  # drop expired snaps

    brands = {d for c in cache.values() if c.get("why") for d in c.get("d", [])}
    for s in snaps:
        c = cache.get(s["id"], {})
        why = c.get("why") or next((f"brand: {d}" for d in c.get("d", []) if d in brands), None)
        if why or s.get("ad"):
            s["ad"] = 1
            print("ad:", s["id"][-20:], why or "sponsored")
    for a in data["accounts"]:
        print(f"{a['u']}: {sum(1 for s in a['snaps'] if s.get('ad'))} ads / {len(a['snaps'])}")
    print(f"read {sum(1 for s in todo if s['id'] in cache)} of {len(todo)} new snaps in {time.time() - DEADLINE + 360:.0f}s")
    json.dump(data, open(STORIES, "w"), ensure_ascii=False, separators=(",", ":"))
    json.dump(cache, open(OCR, "w"), ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
