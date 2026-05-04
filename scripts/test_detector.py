#!/usr/bin/env python3
"""Verify the detector correctly classifies all sample images."""
import sys
from pathlib import Path
import cv2

sys.path.insert(0, str(Path(__file__).parent.parent))
from modules.base_finder.detector import is_loading_screen, _edge_density, _pixel_std

def main():
    loading_dir = Path("data/samples/loading")
    gameplay_dir = Path("data/samples/gameplay")

    print("LOADING SCREENS (should all be True):")
    for img_path in sorted(loading_dir.glob("*.png")):
        img = cv2.imread(str(img_path))
        result = is_loading_screen(img)
        edge_d = _edge_density(img)
        px_std = _pixel_std(img)
        status = "PASS" if result else "FAIL"
        print(f"  [{status}] {img_path.name}: is_loading={result} (edge={edge_d:.4f}, std={px_std:.1f})")

    print("\nGAMEPLAY (should all be False):")
    for img_path in sorted(gameplay_dir.glob("*.png")):
        img = cv2.imread(str(img_path))
        result = is_loading_screen(img)
        edge_d = _edge_density(img)
        px_std = _pixel_std(img)
        status = "PASS" if not result else "FAIL"
        print(f"  [{status}] {img_path.name}: is_loading={result} (edge={edge_d:.4f}, std={px_std:.1f})")

if __name__ == "__main__":
    main()
