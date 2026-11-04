// SPDX-License-Identifier: GPL-2.0-or-later
// Hand-drawn marks for the Sinux overlay (circle, box, underline, arrow).
// Geometry is generated locally from a target region; outlines come from the
// vendored perfect-freehand 1.2.3 (MIT) already used by the presentation layer.
import { getStroke } from '../presentation/upstream/perfect-freehand/index.mjs';

function jitter(seed) {
    let s = seed % 2147483647; if (s <= 0) s += 2147483646;
    return () => { s = s * 16807 % 2147483647; return (s - 1) / 2147483646 - 0.5; };
}

// Returns a list of strokes; each stroke is a list of [x, y, pressure].
export function strokes(shape, x1, y1, x2, y2, seed) {
    const r = jitter(seed || 7);
    const left = Math.min(x1, x2), right = Math.max(x1, x2), top = Math.min(y1, y2), bottom = Math.max(y1, y2);
    const w = Math.max(8, right - left), h = Math.max(8, bottom - top);
    if (shape === 'circle') {
        const cx = (left + right) / 2, cy = (top + bottom) / 2;
        const rx = w / 2 + 14, ry = h / 2 + 12;
        const start = -Math.PI * 0.62 + r() * 0.4, sweep = Math.PI * 2.18;
        const n = 72, pts = [];
        for (let i = 0; i <= n; i++) {
            const t = i / n, a = start + sweep * t;
            const grow = 1 + 0.06 * t + 0.015 * Math.sin(t * 9 + seed);
            pts.push([cx + Math.cos(a) * rx * grow, cy + Math.sin(a) * ry * (grow - 0.02), 0.35 + 0.4 * Math.sin(Math.PI * t)]);
        }
        // Two halves: a single overlapping loop outline does not fill cleanly.
        const mid = Math.floor(n / 2);
        return [pts.slice(0, mid + 1), pts.slice(mid)];
    }
    if (shape === 'box') {
        const pad = 8, L = left - pad, T = top - pad, R = right + pad, B = bottom + pad;
        const corners = [[L, T], [R + 3, T - 1], [R + 1, B + 2], [L - 2, B], [L + 1, T - 3]];
        const pts = [];
        for (let c = 0; c < corners.length - 1; c++) {
            const [ax, ay] = corners[c], [bx, by] = corners[c + 1];
            for (let i = 0; i < 14; i++) {
                const t = i / 14;
                pts.push([ax + (bx - ax) * t + r() * 1.2, ay + (by - ay) * t + r() * 1.2, 0.5]);
            }
        }
        pts.push([...corners[corners.length - 1], 0.3]);
        return [pts];
    }
    if (shape === 'underline') {
        const y = bottom + 6, pts = [];
        for (let i = 0; i <= 32; i++) {
            const t = i / 32;
            pts.push([left - 4 + (w + 8) * t, y + Math.sin(Math.PI * t) * 2.5 + r() * 0.8, 0.2 + 0.55 * Math.sin(Math.PI * t)]);
        }
        return [pts];
    }
    if (shape === 'arrow') {
        const dx = x2 - x1, dy = y2 - y1, len = Math.max(1, Math.hypot(dx, dy));
        const nx = -dy / len, ny = dx / len, bend = Math.min(40, len * 0.12);
        const shaft = [];
        for (let i = 0; i <= 36; i++) {
            const t = i / 36, b = Math.sin(Math.PI * t) * bend;
            shaft.push([x1 + dx * t + nx * b, y1 + dy * t + ny * b, 0.3 + 0.4 * t]);
        }
        const ux = dx / len, uy = dy / len, head = Math.min(26, len * 0.35);
        const wing = (side) => {
            const ax = x2 - ux * head + nx * head * 0.55 * side, ay = y2 - uy * head + ny * head * 0.55 * side;
            const pts = [];
            for (let i = 0; i <= 8; i++) { const t = i / 8; pts.push([ax + (x2 - ax) * t, ay + (y2 - ay) * t, 0.3 + 0.5 * t]); }
            return pts;
        };
        return [shaft, wing(1), wing(-1)];
    }
    return [];
}

// SVG outline of the first `fraction` of a stroke.
export function outline(points, fraction, size) {
    const count = Math.max(2, Math.ceil(points.length * Math.max(0, Math.min(1, fraction))));
    if (fraction <= 0) return '';
    const out = getStroke(points.slice(0, count), {
        size: size || 6, thinning: 0.5, smoothing: 0.6, streamline: 0.3, simulatePressure: false,
        last: fraction >= 1, start: {taper: 10}, end: {taper: fraction >= 1 ? 14 : 0}
    });
    if (!out.length) return '';
    return out.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ') + ' Z';
}
