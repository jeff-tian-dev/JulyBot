#!/usr/bin/env python3
import sys
import os
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

def analyze_image(image_path):
    """Extract signal values from an image."""
    img = cv2.imread(image_path)
    if img is None:
        return None

    # Convert to HSV for V-channel analysis
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    v_channel = hsv[:, :, 2]

    # Overall brightness (V-channel mean)
    mean_v = np.mean(v_channel)

    # Fraction of bright pixels in bottom band (0.78-0.88 of height)
    height = v_channel.shape[0]
    bottom_start = int(height * 0.78)
    bottom_end = int(height * 0.88)
    bottom_band = v_channel[bottom_start:bottom_end, :]
    brightness_threshold = 150
    bright_pixels_ratio = np.mean(bottom_band > brightness_threshold)

    # White/light background (high V, low saturation)
    h_channel = hsv[:, :, 0]
    s_channel = hsv[:, :, 1]
    white_pixels = np.sum((v_channel > 200) & (s_channel < 50))
    white_ratio = white_pixels / (v_channel.shape[0] * v_channel.shape[1])

    # Orange/red pixels (loading screen dragon)
    # H: 0-30 (red) or 160-180 (red wrap), S: 50-255, V: 100-255
    orange_mask = ((h_channel < 30) | (h_channel > 150)) & (s_channel > 50) & (v_channel > 100)
    orange_pixels = np.sum(orange_mask)
    orange_ratio = orange_pixels / (v_channel.shape[0] * v_channel.shape[1])

    return {
        "mean_v": mean_v,
        "bottom_bright_ratio": bright_pixels_ratio,
        "white_ratio": white_ratio,
        "orange_ratio": orange_ratio,
    }

def main():
    loading_dir = Path("data/samples/loading")
    gameplay_dir = Path("data/samples/gameplay")

    print("=" * 70)
    print("LOADING SCREEN SAMPLES")
    print("=" * 70)
    for img_path in sorted(loading_dir.glob("*.png")):
        signals = analyze_image(str(img_path))
        if signals:
            print(f"{img_path.name}")
            print(f"  Mean V-brightness: {signals['mean_v']:.1f}")
            print(f"  Bottom band bright pixels: {signals['bottom_bright_ratio']:.3f}")
            print(f"  White ratio: {signals['white_ratio']:.3f}")
            print(f"  Orange ratio: {signals['orange_ratio']:.3f}")
            print()

    print("=" * 70)
    print("GAMEPLAY SAMPLES")
    print("=" * 70)
    for img_path in sorted(gameplay_dir.glob("*.png")):
        signals = analyze_image(str(img_path))
        if signals:
            print(f"{img_path.name}")
            print(f"  Mean V-brightness: {signals['mean_v']:.1f}")
            print(f"  Bottom band bright pixels: {signals['bottom_bright_ratio']:.3f}")
            print(f"  White ratio: {signals['white_ratio']:.3f}")
            print(f"  Orange ratio: {signals['orange_ratio']:.3f}")
            print()

if __name__ == "__main__":
    main()
