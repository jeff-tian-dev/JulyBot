#!/usr/bin/env python3
"""Scan a YouTube video for loading screen timestamps."""
import sys
from datetime import datetime
from pathlib import Path

import cv2
import yt_dlp

sys.path.insert(0, str(Path(__file__).parent.parent))
from modules.base_finder.detector import is_loading_screen


class TeeWriter:
    """Write to both stdout and a log file, flushing after each write."""
    def __init__(self, log_path):
        self.log_file = open(log_path, "w", encoding="utf-8")

    def write(self, msg=""):
        print(msg)
        self.log_file.write(str(msg) + "\n")
        self.log_file.flush()

    def close(self):
        self.log_file.close()


def _format_time(seconds: float) -> str:
    """Format seconds as MM:SS.mmm."""
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{minutes}:{secs:02d}.{millis:03d}"


def scan_video(video_url: str, sample_interval: float = 1.0):
    """Download and scan a video for loading screens and gameplay transitions.

    Args:
        video_url: YouTube URL
        sample_interval: seconds between initial samples (default 1s)
    """
    # Set up log file in logs/ folder
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = logs_dir / f"scan_{timestamp_str}.log"
    log = TeeWriter(str(log_path))
    log.write(f"Scan started: {datetime.now().isoformat()}")
    log.write(f"Log file: {log_path}")
    log.write(f"Video: {video_url}")
    log.write("")

    # Extract stream URL using yt-dlp - prefer 720p video-only stream
    log.write(f"Resolving stream URL...")
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestvideo[height<=720]/best[height<=720]",
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        stream_url = info.get("url")
        if not stream_url:
            for fmt in info.get("formats", []):
                if fmt.get("vcodec") != "none" and fmt.get("height", 0) >= 480:
                    stream_url = fmt["url"]
                    break
        if not stream_url:
            log.write("ERROR: Could not find video stream")
            log.close()
            return

    # Open stream with OpenCV
    log.write("Opening stream...")
    cap = cv2.VideoCapture(stream_url)
    if not cap.isOpened():
        log.write("ERROR: Failed to open stream")
        log.close()
        return

    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_skip = max(1, int(fps * sample_interval))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    total_seconds = total_frames / fps

    log.write(f"FPS: {fps}, Resolution: {width}x{height}, Total duration: {total_seconds:.1f}s")
    log.write(f"Sampling every {sample_interval}s ({frame_skip} frames)")
    log.write("")
    log.write("Scanning for loading screens...")
    log.write("-" * 70)

    frame_num = 0
    transitions = []
    last_loading_frame = None
    last_progress_log = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Sample at intervals
        if frame_num % frame_skip == 0:
            timestamp = frame_num / fps
            is_loading = is_loading_screen(frame)

            # Periodic progress log every 60s of video
            if timestamp - last_progress_log >= 60.0:
                log.write(f"  ... progress: {_format_time(timestamp)} / {_format_time(total_seconds)}")
                last_progress_log = timestamp

            # If we detect a loading screen, sweep forward to find the gameplay transition
            if is_loading and last_loading_frame != frame_num:
                log.write(f"[{_format_time(timestamp)}] Loading screen detected, sweeping for transition...")
                last_loading_frame = frame_num

                # Sweep forward frame-by-frame to find gameplay
                sweep_frame = frame_num + 1
                sweep_success = False
                while sweep_frame < total_frames:
                    ret_sweep, sweep_frame_data = cap.read()
                    if not ret_sweep:
                        break

                    if not is_loading_screen(sweep_frame_data):
                        transition_time = sweep_frame / fps
                        log.write(f"  -> Gameplay starts at {_format_time(transition_time)}")
                        transitions.append({
                            "loading_start": timestamp,
                            "gameplay_start": transition_time,
                            "duration": transition_time - timestamp
                        })
                        sweep_success = True
                        frame_num = sweep_frame
                        break

                    sweep_frame += 1

                if not sweep_success:
                    log.write("  -> Could not find gameplay transition (end of video?)")

        frame_num += 1

    cap.release()

    log.write("-" * 70)
    log.write(f"")
    log.write(f"Found {len(transitions)} attack transitions")
    if transitions:
        log.write("Transitions:")
        for i, trans in enumerate(transitions, 1):
            log.write(f"  {i}. Loading: {_format_time(trans['loading_start'])} -> "
                      f"Gameplay: {_format_time(trans['gameplay_start'])} "
                      f"(duration: {trans['duration']:.2f}s)")

    log.write(f"")
    log.write(f"Scan completed: {datetime.now().isoformat()}")
    log.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scan_video.py <youtube_url> [sample_interval_seconds]")
        print("Example: python scan_video.py 'https://www.youtube.com/watch?v=...' 1")
        sys.exit(1)

    url = sys.argv[1]
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    scan_video(url, interval)
