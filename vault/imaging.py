"""Auto-crop pipeline for vault intake images.

Takes a scan or phone photo of one card (raw or slabbed) and returns a
straightened, cropped, lightly enhanced image at a fixed size, plus warnings
when it isn't confident so staff can check that crop by hand.

Strategy:
  1. Find the card as the largest four-sided outline in the frame
     (works for scanner output and photos with background around the card).
  2. If no clean outline is found, the card probably fills the frame; fit a
     straight line to each edge instead (works for tight phone shots).
  3. Perspective-correct to an exact rectangle, then apply the same light
     contrast and sharpening to every card so they look consistent.
"""
import cv2
import numpy as np

# Output sizes (width x height). Raw cards are 63 x 88 mm; PSA-style slabs are ~0.605 wide/high.
SIZES = {"raw": (630, 880), "graded": (620, 1024)}
THUMB_WIDTH = 180
RATIOS = {"raw": 63 / 88, "graded": 0.605}


def _order_corners(pts):
    """Return corners ordered top-left, top-right, bottom-right, bottom-left."""
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.float32([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]])


def _quad_aspect(quad):
    tl, tr, br, bl = quad
    w = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2
    h = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2
    return (w / h) if h else 0, w, h


def find_outline(img, kind="raw"):
    """Largest 4-sided contour that looks like a card. Returns (quad, score) or (None, 0)."""
    h, w = img.shape[:2]
    scale = 900 / max(h, w)
    small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else img.copy()
    scale = min(scale, 1.0)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    sh, sw = gray.shape
    target = RATIOS[kind]
    best, best_score = None, 0.0

    # Ways to separate card from background, most precise first:
    #   1. distance from the background color (sampled at the image border) — exact edges on scanner beds and mats
    #   2. brightness split (Otsu), both polarities
    #   3. edge detection — robust but its outline sits a few pixels outside the card, so it scores lower
    masks = []
    border = np.concatenate([small[:6].reshape(-1, 3), small[-6:].reshape(-1, 3), small[:, :6].reshape(-1, 3), small[:, -6:].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    spread = np.median(np.abs(border - bg), axis=0).max()
    diff = np.abs(cv2.GaussianBlur(small, (5, 5), 0).astype(np.float32) - bg).max(axis=2)
    masks.append(((diff > max(18.0, 4 * spread)).astype(np.uint8) * 255, 1.0))
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    masks.append((otsu, 0.97))
    masks.append((255 - otsu, 0.97))
    for lo, hi in ((40, 120), (15, 45)):
        edges = cv2.Canny(gray, lo, hi)
        masks.append((cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2), 0.85))

    for mask, weight in masks:
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            area = cv2.contourArea(c)
            frac = area / (sh * sw)
            if frac < 0.12 or frac > 0.97:
                continue
            approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
            if len(approx) == 4 and cv2.isContourConvex(approx):
                quad = _order_corners(approx)
            else:
                quad = _order_corners(cv2.boxPoints(cv2.minAreaRect(c)))
                if area / max(cv2.contourArea(quad), 1) < 0.9:
                    continue  # not rectangular enough
            ratio, _, _ = _quad_aspect(quad)
            ratio = min(ratio, 1 / ratio) if ratio else 0
            ratio_err = abs(ratio - target) / target
            if ratio_err > 0.3:  # generous: photos taken at an angle squash the ratio
                continue
            score = frac * (1 - ratio_err) * weight
            if score > best_score:
                best, best_score = _refine(c, quad) / scale, score
    return best, best_score


def _refine(contour, quad):
    """Fit a straight line to each side's contour points and intersect them for sub-pixel corners."""
    pts = contour.reshape(-1, 2).astype(np.float32)
    lines = []
    for i in range(4):
        a, b = quad[i], quad[(i + 1) % 4]
        ab = b - a
        length = np.linalg.norm(ab)
        if length < 1:
            return quad
        rel = pts - a
        t = (rel @ ab) / (length ** 2)
        dist = np.abs(ab[0] * rel[:, 1] - ab[1] * rel[:, 0]) / length
        side = pts[(t > 0.1) & (t < 0.9) & (dist < max(3.0, 0.03 * length))]
        if len(side) < 5:
            return quad
        vx, vy, x0, y0 = cv2.fitLine(side, cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        lines.append((np.array([x0, y0]), np.array([vx, vy])))
    corners = []
    for i in range(4):
        (p1, d1), (p2, d2) = lines[i - 1], lines[i]
        A = np.array([d1, -d2]).T
        if abs(np.linalg.det(A)) < 1e-6:
            return quad
        s = np.linalg.solve(A, p2 - p1)[0]
        corners.append(p1 + s * d1)
    refined = np.float32(corners)
    return refined if np.max(np.abs(refined - quad)) < 0.05 * max(np.ptp(quad[:, 0]), np.ptp(quad[:, 1])) else quad


def _touches_border(quad, w, h, margin=0.015):
    m = margin * max(w, h)
    return bool(np.any(quad[:, 0] < m) or np.any(quad[:, 0] > w - m) or np.any(quad[:, 1] < m) or np.any(quad[:, 1] > h - m))


def fit_edges(img, kind="raw"):
    """Fallback for a card that fills the frame: fit a line to each edge from brightness changes."""
    g = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (3, 3), 0).astype(np.float32)
    h, w = g.shape
    band = max(20, int(0.08 * min(h, w)))
    thr = max(45.0, float(np.percentile(g, 20)) * 0.8)

    def first_bright(line):
        idx = np.where(line > thr)[0]
        return int(idx[0]) if len(idx) else 0

    xs = range(int(w * 0.08), int(w * 0.92), max(4, w // 40))
    ys = range(int(h * 0.08), int(h * 0.92), max(4, h // 40))
    top = [(x, first_bright(g[:band, x])) for x in xs]
    bottom = [(x, h - 1 - first_bright(g[::-1][:band, x])) for x in xs]
    left = [(first_bright(g[y, :band]), y) for y in ys]
    right = [(w - 1 - first_bright(g[y, ::-1][:band]), y) for y in ys]

    def robust_fit(pts, horizontal):
        a = np.array(pts, dtype=np.float64)
        x, y = (a[:, 0], a[:, 1]) if horizontal else (a[:, 1], a[:, 0])
        m, b = np.polyfit(x, y, 1)
        resid = np.abs(y - (m * x + b))
        keep = resid <= max(2.0, np.percentile(resid, 75))
        return np.polyfit(x[keep], y[keep], 1)

    T, B = robust_fit(top, True), robust_fit(bottom, True)
    L, R = robust_fit(left, False), robust_fit(right, False)

    def meet(hl, vl):
        a1, b1 = hl
        a2, b2 = vl
        y = (a1 * b2 + b1) / (1 - a1 * a2)
        return [a2 * y + b2, y]

    return np.float32([meet(T, L), meet(T, R), meet(B, R), meet(B, L)])


def enhance(img):
    """The same light touch for every card: local contrast, a little color and sharpness."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=1.4, tileGridSize=(8, 8)).apply(l)
    out = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)
    hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] = np.clip(hsv[..., 1] * 1.06, 0, 255)
    out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    blur = cv2.GaussianBlur(out, (0, 0), 1.0)
    return cv2.addWeighted(out, 1.3, blur, -0.3, 0)


def process_card(data, kind="raw", enhance_image=True):
    """Process one image (bytes). Returns dict with jpeg bytes for image + thumb, and warnings list."""
    if kind not in SIZES:
        raise ValueError("kind must be 'raw' or 'graded'")
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Could not read the image. Upload a JPG or PNG.")
    h, w = img.shape[:2]
    warnings = []
    if min(h, w) < 400:
        warnings.append("Low resolution: rescan at 300 dpi or take a closer photo.")

    quad, score = find_outline(img, kind)
    method = "outline"
    if quad is not None and _touches_border(quad, w, h):
        # The card fills the frame (tight phone photo): edge fitting straightens it better.
        quad, method = fit_edges(img, kind), "edges"
        ratio, _, _ = _quad_aspect(quad)
        ratio = min(ratio, 1 / ratio) if ratio else 0
        if abs(ratio - RATIOS[kind]) / RATIOS[kind] > 0.12:
            warnings.append("Card shape looks off. Check the crop.")
    elif quad is None:
        method = "edges"
        quad = fit_edges(img, kind)
        warnings.append("Card edge not clearly separated from the background. Check the crop.")

    ratio, qw, qh = _quad_aspect(quad)
    if qw > qh * 1.05:  # card is lying sideways: rotate corners so output is portrait
        quad = np.float32([quad[3], quad[0], quad[1], quad[2]])
    out_w, out_h = SIZES[kind]
    corners_out = np.float32([[0, 0], [out_w, 0], [out_w, out_h], [0, out_h]])
    M = cv2.getPerspectiveTransform(quad.astype(np.float32), corners_out)
    warped = cv2.warpPerspective(img, M, (out_w, out_h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

    # Corners far outside the frame mean part of the card was cut off in the scan.
    pad = 0.02 * max(h, w)
    if np.any(quad[:, 0] < -pad) or np.any(quad[:, 0] > w + pad) or np.any(quad[:, 1] < -pad) or np.any(quad[:, 1] > h + pad):
        warnings.append("Part of the card edge is outside the image. Leave a margin around the card.")

    if enhance_image:
        warped = enhance(warped)
    thumb = cv2.resize(warped, (THUMB_WIDTH, int(THUMB_WIDTH * out_h / out_w)), interpolation=cv2.INTER_AREA)
    ok1, full = cv2.imencode(".jpg", warped, [cv2.IMWRITE_JPEG_QUALITY, 90])
    ok2, small = cv2.imencode(".jpg", thumb, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not (ok1 and ok2):
        raise RuntimeError("Could not encode processed image.")
    return {
        "image": full.tobytes(),
        "thumb": small.tobytes(),
        "warnings": warnings,
        "method": method,
        "corners": quad.round(1).tolist(),
    }
