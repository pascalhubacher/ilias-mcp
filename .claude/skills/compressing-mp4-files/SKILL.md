---
name: compressing-mp4-files
description: Compress mp4 files in DOWNLOAD_DIR (or a specific course subfolder) to under 200 MiB using ffmpeg, so they can be uploaded to NotebookLM. Compressed files are saved with "-compressed" appended to the filename stem. Use when the user wants to compress video files from ILIAS courses.
---

## Compress MP4 files workflow

The user may optionally specify a course name or path. Parse the invocation args:
- If args contain a course name or path → compress only that course's subfolder
- If no args → compress all mp4 files under DOWNLOAD_DIR

### Step 1 – Read configuration

Read the `.env` file in the project root and extract `DOWNLOAD_DIR`. This is the root folder where ILIAS downloads are stored.

### Step 2 – Locate target directory

- If a course argument was provided: the target is `DOWNLOAD_DIR/<course-arg>`. If the directory does not exist, tell the user and list available subdirectories with `ls` (Bash tool) so they can pick the right name.
- If no argument: the target is the full `DOWNLOAD_DIR`.

### Step 3 – Find mp4 files and measure their sizes

Use a single Python one-liner (via Bash) to discover all mp4 files, exclude already-compressed ones, and report their exact sizes. Python is always available in this project and handles Windows paths with special characters correctly on all platforms.

```bash
python -c "
import os, sys
root = sys.argv[1]
for dirpath, _, filenames in os.walk(root):
    for f in filenames:
        if f.lower().endswith('.mp4') and not f.lower().endswith('-compressed.mp4'):
            p = os.path.join(dirpath, f)
            print(os.path.getsize(p), p)
" "<TARGET_DIR>"
```

Each output line is `<size_in_bytes> <absolute_path>`. Parse it to build the work list.

For each file:
- Convert size to MiB: `size_mib = size_bytes / (1024 * 1024)`
- If size_mib ≤ 200: skip with status "skipped (≤ 200 MiB)"
- Otherwise: add to the compression queue

### Step 4 – Check tool availability

Two tools are required: `ffmpeg` and `ffprobe` (shipped together). Run `ffmpeg -version` and `ffprobe -version` to check.

If either is missing, show the full install instructions for all three platforms and stop — do not proceed without them:

**Windows**
```
winget install Gyan.FFmpeg
```
Or download a pre-built build from https://www.gyan.dev/ffmpeg/builds/ (choose `ffmpeg-release-full.7z`), extract it, and add the `bin\` folder to your `PATH` environment variable.

**macOS**
```
brew install ffmpeg
```
(Requires Homebrew — https://brew.sh if not installed.)

**Linux (Debian / Ubuntu)**
```
sudo apt update && sudo apt install ffmpeg
```
Other distros: `sudo dnf install ffmpeg` (Fedora/RHEL), `sudo pacman -S ffmpeg` (Arch).

### Step 5 – Compress each file

For each mp4 that needs compression, calculate the target bitrate to stay under 200 MiB:

```
target_size_bits = 200 * 1024 * 1024 * 8        # 200 MiB in bits
duration_s       = <video duration in seconds>
target_bitrate   = int(target_size_bits / duration_s * 0.95)  # 5 % safety margin
audio_bitrate    = 128_000                        # 128 kbps audio
video_bitrate    = target_bitrate - audio_bitrate
```

Get the duration using:
```bash
ffprobe -v error -show_entries format=duration -of csv=p=0 "<input_path>"
```

The output path is `<same_dir>/<stem>-compressed.mp4`.

Run ffmpeg with two-pass encoding for accurate size targeting:

```bash
# Pass 1 (analysis only, no output file)
ffmpeg -y -i "<input>" -c:v libx264 -b:v <video_bitrate>k -pass 1 -an -f null /dev/null

# Pass 2 (encode)
ffmpeg -y -i "<input>" -c:v libx264 -b:v <video_bitrate>k -pass 2 -c:a aac -b:a 128k "<output>"
```

Replace `/dev/null` with `NUL` on Windows.

After encoding, verify the output size is ≤ 200 MiB. If not (bitrate estimate was off), warn the user but keep the file.

Clean up ffmpeg passlog files (`ffmpeg2pass-0.log`, `ffmpeg2pass-0.log.mbtree`) after each video.

### Step 6 – Report results

Print a summary table:

```
## Compression summary

| File | Original | Compressed | Ratio | Status |
|------|----------|------------|-------|--------|
| lecture01.mp4 | 450 MiB | 198 MiB | 44% | ✓ done |
| intro.mp4 | 95 MiB | — | — | skipped (≤200 MiB) |
```

Total: X compressed, Y skipped, Z errors.
