---
name: compressing-mp4-files
description: Compress mp4 files in DOWNLOAD_DIR (or a specific course subfolder) to under 200 MiB using ffmpeg, so they can be uploaded to NotebookLM. Compressed files are saved with a two-digit date-based prefix (01_, 02_, ...) prepended to the original filename, ordered oldest-first by embedded mp4 creation_time metadata (falls back to filesystem mtime). Use when the user wants to compress video files from ILIAS courses.
---

## Compress MP4 files workflow

The user may optionally specify a course name or path. Parse the invocation args:
- If args contain a course name or path → compress only that course's subfolder
- If no args → compress all mp4 files under DOWNLOAD_DIR

### Step 1 – Read configuration

Find the `ilias-mcp` project path dynamically by running:

```bash
claude mcp list
```

Parse the output for the `ilias-mcp` entry and extract the `--directory` argument (e.g. `uv --directory /some/path run ilias-mcp`). That directory is the project root. Read `.env` from there and extract `DOWNLOAD_DIR`.

If `ilias-mcp` is not listed, fall back to looking for `.env` in the current working directory.

### Step 2 – Locate target directory

- If a course argument was provided: the target is `DOWNLOAD_DIR/<course-arg>`. If the directory does not exist, tell the user and list available subdirectories with `ls` (Bash tool) so they can pick the right name.
- If no argument: the target is the full `DOWNLOAD_DIR`.

### Step 3 – Find mp4 files, assign date-based numbers, and measure sizes

Use a single Python script (via Bash) to discover all mp4 files **per directory**, detect already-processed counterparts, and assign the next free numbers to new files. Python is always available in this project and handles Windows paths with special characters correctly on all platforms.

**Numbering rule:** New files always receive numbers **above the current maximum** already used in the directory — existing prefixes are never shifted. Among multiple new files added in the same run, they are sorted oldest-first by their embedded `creation_time` metadata (read via `ffprobe`), falling back to filesystem mtime. This ensures a podcast downloaded later that is chronologically older never steals a number already assigned to an earlier-processed file.

```bash
python -c "
import os, sys, re, subprocess, json

def mp4_creation_time(path):
    '''Return the mp4 creation_time tag as a sortable string, or None.'''
    try:
        out = subprocess.check_output(
            ['ffprobe', '-v', 'quiet', '-print_format', 'json',
             '-show_entries', 'format_tags=creation_time', path],
            stderr=subprocess.DEVNULL
        )
        data = json.loads(out)
        return data.get('format', {}).get('tags', {}).get('creation_time')
    except Exception:
        return None

def sort_key(path):
    ts = mp4_creation_time(path)
    if ts:
        return (0, ts)           # prefer embedded tag
    return (1, str(os.path.getmtime(path)))  # fallback: filesystem mtime

root = sys.argv[1]

for dirpath, dirs, filenames in os.walk(root):
    all_names_lower = set(f.lower() for f in filenames)

    # Find the highest prefix number already in use in this directory
    max_used = 0
    for name in all_names_lower:
        m = re.match(r'^(\d{2})_', name)
        if m:
            max_used = max(max_used, int(m.group(1)))

    # Only consider original (non-prefixed) mp4 files
    originals = [
        f for f in filenames
        if f.lower().endswith('.mp4') and not re.match(r'^\d{2}_', f)
    ]
    if not originals:
        continue

    # Separate already-processed from new files
    new_files = []
    for f in originals:
        already_exists = any(
            re.match(r'^\d{2}_' + re.escape(f.lower()), name)
            for name in all_names_lower
        )
        if already_exists:
            print('ALREADY_COMPRESSED', os.path.join(dirpath, f))
        else:
            new_files.append(f)

    # Sort new files by creation_time (oldest first), fallback to mtime
    new_files.sort(key=lambda f: sort_key(os.path.join(dirpath, f)))

    # Assign numbers starting after the current maximum
    for offset, f in enumerate(new_files):
        idx = max_used + 1 + offset
        p = os.path.join(dirpath, f)
        print(os.path.getsize(p), idx, p)
" "<TARGET_DIR>"
```

Each output line is either:
- `ALREADY_COMPRESSED <absolute_path>` — a `<NN>_<original_name>.mp4` counterpart already exists (any index) → skip with status "skipped (already processed)"
- `<size_in_bytes> <sequential_index> <absolute_path>` — parse to build the work list

For each file in the work list:
- Convert size to MiB: `size_mib = size_bytes / (1024 * 1024)`
- If size_mib ≤ 200: **copy** the file as `<NN>_<original_filename>` (no re-encoding needed) → status "copied (≤ 200 MiB)"
- Otherwise: add to the compression queue, keeping the `<sequential_index>` associated with the file

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

### Step 5 – Copy or compress each file

For each file in the work list, the output path is always `<same_dir>/<NN>_<original_filename>`.

**If size_mib ≤ 200** — copy the file as-is (no re-encoding):

```bash
# Windows
copy "<input>" "<output>"

# macOS / Linux
cp "<input>" "<output>"
```

**If size_mib > 200** — compress. Calculate the target bitrate to stay under 200 MiB:

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

The output path is `<same_dir>/<NN>_<original_filename>` where `<NN>` is the two-digit sequential index assigned in Step 3 (e.g. `01_lecture.mp4`, `02_seminar_xyz.mp4`).

Run ffmpeg with two-pass encoding for accurate size targeting. Always pass `-passlogfile "<same_dir>/ffmpeg2pass"` so that the temporary passlog files are written into the **same directory as the input video** — never into the shell's working directory.

```bash
# Pass 1 (analysis only, no output file)
ffmpeg -y -i "<input>" -c:v libx264 -b:v <video_bitrate>k -pass 1 -passlogfile "<same_dir>/ffmpeg2pass" -an -f null /dev/null

# Pass 2 (encode)
ffmpeg -y -i "<input>" -c:v libx264 -b:v <video_bitrate>k -pass 2 -passlogfile "<same_dir>/ffmpeg2pass" -c:a aac -b:a 128k "<output>"
```

Replace `/dev/null` with `NUL` on Windows.

After encoding, verify the output size is ≤ 200 MiB. If not (bitrate estimate was off), warn the user but keep the file.

Clean up the passlog files from the video's directory after each video: `<same_dir>/ffmpeg2pass-0.log` and `<same_dir>/ffmpeg2pass-0.log.mbtree`.

### Step 6 – Report results

Print a summary table:

```
## Compression summary

| Original file | Output file | Original | Compressed | Ratio | Status |
|---------------|-------------|----------|------------|-------|--------|
| lecture_xyz.mp4 | 01_lecture_xyz.mp4 | 450 MiB | 198 MiB | 44% | ✓ compressed |
| seminar_abc.mp4 | 02_seminar_abc.mp4 | 95 MiB | 95 MiB | — | ✓ copied (≤200 MiB) |
| intro.mp4 | 03_intro.mp4 | 380 MiB | — | — | skipped (already processed) |
```

Total: X compressed, Y skipped, Z errors.
