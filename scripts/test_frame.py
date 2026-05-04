#!/usr/bin/env python3
import sys
from pathlib import Path
import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from modules.base_finder.detector import is_loading_screen, _orange_ratio, _frame_brightness

# Load the screenshot you just uploaded
img = cv2.imread("d:\\JulyBot\\test_gameplay_frame.png")

if img is None:
    print("ERROR: Could not load image")
    sys.exit(1)

brightness = _frame_brightness(img)
orange = _orange_ratio(img)
is_loading = is_loading_screen(img)

print("Frame Analysis:")
print(f"  Mean V-brightness: {brightness:.1f}")
print(f"  Orange ratio: {orange:.4f}")
print(f"  Is loading screen: {is_loading}")
print()
print("Thresholds:")
print(f"  BRIGHT_LOADING_THRESHOLD: 200")
print(f"  ORANGE_RATIO_THRESHOLD: 0.08")
print()
if brightness > 200:
    print("  -> Flagged as BRIGHT loading screen")
elif orange < 0.08:
    print("  -> Flagged as DARK loading screen (orange_ratio < 0.08)")
else:
    print("  -> Correctly identified as GAMEPLAY")
