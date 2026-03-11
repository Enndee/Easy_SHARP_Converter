# Easy SHARP Converter

Easy SHARP Converter is a fullscreen Windows GUI for converting images and existing splat files with Apple's SHARP model and `gsbox`.

## Features

- Batch image conversion with parallel SHARP inference
- Direct conversion for `.ply`, `.spx`, `.spz`, and `.sog`
- Output export as `.ply`, `.spx`, `.spz`, or `.sog`
- Optional transform, scale, and crop post-processing
- Built-in file browser with thumbnails, metadata, and multi-select
- Configurable splat viewer launch on double click or Enter

## New PC Setup

1. Install Anaconda or Miniconda.
2. Install Git for Windows.
3. Run `Setup_NewPC.bat`.
4. Start `Easy_SHARP_Converter.exe` or `Launch_Easy_SHARP.bat`.

## Build

Run `build_exe.bat` from a machine where the `sharp` conda environment already exists. The script builds the executable and assembles a versioned release package under `release_pkg/`.

## Release Package Contents

- `Easy_SHARP_Converter.exe`
- `Launch_Easy_SHARP.bat`
- `Launch_SHARP_GUI.bat`
- `Setup_NewPC.bat`
- `gsbox.exe`
- `splatapult/build/Release/`
