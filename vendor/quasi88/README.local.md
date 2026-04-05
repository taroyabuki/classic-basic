# QUASI88 Local Build Notes

## Source
Based on QUASI88-SDL2 from https://github.com/UMJAMMER/QUASI88-SDL2
Version: SDL2 port (based on 0.6.4)

## Build Commands

```bash
cd vendor/quasi88
make SDL2_CONFIG=./sdl2-config-linux
```

## Required Dependencies

```bash
apt-get install -y build-essential pkg-config libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev
```

## Modifications Made

1. Created `sdl2-config-linux` script to replace macOS-specific config
2. Modified Makefile to add `-lm` linker flag for math library

## Monitor Mode

The `-monitor` option should be available for monitor mode access.
Monitor commands include:
- `textscr` - Output text screen contents
- `loadbas <filename> [ASCII]` - Load BASIC program
- `savebas <filename>` - Save BASIC program

## ROM Requirements

Place ROM files in the `ROM/` subdirectory:
- N88.ROM (32KB)
- N88_0.ROM, N88_1.ROM, N88_2.ROM, N88_3.ROM (8KB each)
- N80.ROM (32KB) 
- DISK.ROM (8KB)
- KANJI1.ROM, KANJI2.ROM (optional, 128KB each)
- FONT.ROM (optional)

Alternatively, use `-romdir <path>` to specify ROM directory.
