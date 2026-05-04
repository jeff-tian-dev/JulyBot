#!/usr/bin/env python3
"""Test multiple distinguishing signals to find what reliably separates
loading screens from gameplay."""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))


def analyze_image(image_path):
    img = cv2.imread(image_path)
    if img is None:
        return None

    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 1. Mean brightness (V channel)
    mean_v = float(hsv[:, :, 2].mean())

    # 2. Pixel standard deviation (whole frame)
    pixel_std = float(gray.std())

    # 3. Edge density (Canny edge detection)
    edges = cv2.Canny(gray, 50, 150)
    edge_density = float(edges.mean()) / 255.0

    # 4. Color variance (HSV hue std dev — measures color diversity)
    hue_std = float(hsv[:, :, 0].std())
    sat_mean = float(hsv[:, :, 1].mean())

    # 5. Histogram entropy (low = uniform image like loading screen)
    hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
    hist = hist / hist.sum()
    hist_nonzero = hist[hist > 0]
    entropy = float(-np.sum(hist_nonzero * np.log2(hist_nonzero)))

    # 6. White ratio (high V, low S)
    white_pixels = np.sum((hsv[:, :, 2] > 200) & (hsv[:, :, 1] < 50))
    white_ratio = white_pixels / (h * w)

    # 7. Center vs edges variance (loading has uniform edges, varied center)
    center = img[h//4:3*h//4, w//4:3*w//4]
    edges_region = np.concatenate([
        img[:h//4].reshape(-1, 3),
        img[3*h//4:].reshape(-1, 3),
        img[h//4:3*h//4, :w//4].reshape(-1, 3),
        img[h//4:3*h//4, 3*w//4:].reshape(-1, 3),
    ])
    center_std = float(center.std())
    edges_std = float(edges_region.std())

    # 8. Dominant color analysis - peak in histogram
    # Loading screens have ONE dominant color (white or dark gray)
    hist_peak_ratio = float(hist.max())  # Fraction of pixels at most common gray value

    return {
        "mean_v": mean_v,
        "pixel_std": pixel_std,
        "edge_density": edge_density,
        "hue_std": hue_std,
        "sat_mean": sat_mean,
        "entropy": entropy,
        "white_ratio": white_ratio,
        "center_std": center_std,
        "edges_std": edges_std,
        "hist_peak_ratio": hist_peak_ratio,
    }


def main():
    loading_dir = Path("data/samples/loading")
    gameplay_dir = Path("data/samples/gameplay")

    loading_results = []
    gameplay_results = []

    print(f"\n{'='*100}")
    print(f"SIGNAL COMPARISON: LOADING SCREENS vs GAMEPLAY")
    print(f"{'='*100}\n")

    print(f"{'File':<28} {'V':>6} {'PxStd':>7} {'EdgeD':>7} {'HueStd':>7} {'SatMn':>6} {'Entr':>5} {'White':>6} {'Peak':>6}")
    print("-" * 100)

    print("\nLOADING SCREENS:")
    for img_path in sorted(loading_dir.glob("*.png")):
        s = analyze_image(str(img_path))
        if s:
            loading_results.append(s)
            print(f"{img_path.name:<28} {s['mean_v']:>6.1f} {s['pixel_std']:>7.1f} {s['edge_density']:>7.3f} {s['hue_std']:>7.1f} {s['sat_mean']:>6.1f} {s['entropy']:>5.2f} {s['white_ratio']:>6.3f} {s['hist_peak_ratio']:>6.3f}")

    print("\nGAMEPLAY:")
    for img_path in sorted(gameplay_dir.glob("*.png")):
        s = analyze_image(str(img_path))
        if s:
            gameplay_results.append(s)
            print(f"{img_path.name:<28} {s['mean_v']:>6.1f} {s['pixel_std']:>7.1f} {s['edge_density']:>7.3f} {s['hue_std']:>7.1f} {s['sat_mean']:>6.1f} {s['entropy']:>5.2f} {s['white_ratio']:>6.3f} {s['hist_peak_ratio']:>6.3f}")

    # Find which signals separate cleanly
    print(f"\n{'='*100}")
    print(f"SEPARATION ANALYSIS (lower = better separation)")
    print(f"{'='*100}\n")

    signals = ['mean_v', 'pixel_std', 'edge_density', 'hue_std', 'sat_mean',
               'entropy', 'white_ratio', 'hist_peak_ratio']

    print(f"{'Signal':<20} {'Loading min':>12} {'Loading max':>12} {'Gameplay min':>13} {'Gameplay max':>13} {'Overlap?':>10}")
    print("-" * 90)
    for sig in signals:
        l_min = min(r[sig] for r in loading_results)
        l_max = max(r[sig] for r in loading_results)
        g_min = min(r[sig] for r in gameplay_results)
        g_max = max(r[sig] for r in gameplay_results)
        overlap = "YES" if (l_min <= g_max and g_min <= l_max) else "NO"
        print(f"{sig:<20} {l_min:>12.3f} {l_max:>12.3f} {g_min:>13.3f} {g_max:>13.3f} {overlap:>10}")


if __name__ == "__main__":
    main()
