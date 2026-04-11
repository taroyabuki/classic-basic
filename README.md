# Terminal Classic BASICs

通常のターミナル上で古典的な Microsoft BASIC 系を動かすための実用構成です。

冒頭の整理として、このリポジトリで扱っている BASIC は次のとおりです。

| BASIC | 時期 | 代表的な対象コンピュータ | 浮動小数点数の規格 |
| --- | --- | --- | --- |
| 6502 BASIC | 1977 | Ohio Scientific C1P / Challenger 1P 系 | Microsoft 6502 BASIC small FP (`CONFIG_SMALL`) |
| BASIC-80 | 1977 | 汎用 CP/M-80 マシン | Microsoft Binary Format (MBF) |
| N-BASIC | 1979 | NEC PC-8001 | Microsoft Binary Format (MBF) |
| N88-BASIC | 1981 | NEC PC-8801 | Microsoft Binary Format (MBF) |
| FM-7 F-BASIC | 1982 | Fujitsu FM-7 | Microsoft Binary Format (MBF) |
| FM-11 F-BASIC | 1982 | Fujitsu FM-11 | Microsoft Binary Format (MBF) |
| GW-BASIC | 1983 | IBM PC 互換機 | Microsoft Binary Format (MBF) |
| MSX-BASIC | 1983 | MSX / MSX2 / MSX2+ 系 | BCD (packed BCD, 底 10) |
| QBasic | 1991 | MS-DOS 5 世代の IBM PC 互換機 | IEEE 754 binary64 |
| Grant BASIC | 2012 | Grant Searle Simple Z80 SBC | Microsoft Binary Format (MBF) |

## セットアップ

全部まとめて準備する場合は `./setup/all.sh` を使います。個別に準備したいときだけ、下の各スクリプトを直接実行してください。

| BASIC | スクリプト | 実行方法 |
| --- | --- | --- |
| 全 BASIC | [all.sh](setup/all.sh) | `./setup/all.sh` |
| 6502 BASIC | [6502.sh](setup/6502.sh) | `./setup/6502.sh` |
| BASIC-80 | [basic80.sh](setup/basic80.sh) | `./setup/basic80.sh` |
| N-BASIC | [nbasic.sh](setup/nbasic.sh) | `./setup/nbasic.sh` |
| N88-BASIC | [n88basic.sh](setup/n88basic.sh) | `./setup/n88basic.sh` |
| FM-7 F-BASIC | [fm7basic.sh](setup/fm7basic.sh) | `./setup/fm7basic.sh` |
| FM-11 F-BASIC | [fm11basic.sh](setup/fm11basic.sh) | `./setup/fm11basic.sh` |
| GW-BASIC | [gwbasic.sh](setup/gwbasic.sh) | `./setup/gwbasic.sh` |
| MSX-BASIC | [msxbasic.sh](setup/msxbasic.sh) | `./setup/msxbasic.sh` |
| QBasic | [qbasic.sh](setup/qbasic.sh) | `./setup/qbasic.sh` |
| Grant BASIC | [grantsbasic.sh](setup/grantsbasic.sh) | `./setup/grantsbasic.sh` |

主な取得元:

- `N-BASIC`: `https://archive.org/download/mame-0.221-roms-merged/pc8001.zip`
- `N88-BASIC`: Neo Kobe emulator pack (`https://archive.org/download/neo-kobe-emulator-pack-2013-08-17.7z/Neo%20Kobe%20emulator%20pack%202013-08-17.7z`)
  - 実行バイナリの emulator 本体は local `vendor/quasi88/` に同梱しており、official QUASI88 `0.7.3` release (`https://www.eonet.ne.jp/~showtime/quasi88/download.html`) をベースに、この repo 用の control bridge を加えています
- `FM-7 F-BASIC`: `https://archive.org/download/mame-0.221-roms-merged/fm7.zip`
- `FM-11 F-BASIC`: `https://archive.org/download/mame-0.221-roms-merged/fm11.zip`
- `MSX-BASIC`: `https://archive.org/download/mame-0.221-roms-merged/vg802020.zip`

## 対話的な利用

`./run/スクリプトファイル` で通常の対話モードを起動します。対話モードは `Ctrl-D` で終了します。QBasic は full-screen IDE をそのまま流さず、host 側の text shell として動きます。行番号付き入力、`LIST`、`RUN`、`NEW`、および direct statement を text-only で扱い、実行自体は real QBasic の batch backend を使います。

| BASIC | スクリプト | 実行方法 |
| --- | --- | --- |
| 6502 BASIC | [6502.sh](run/6502.sh) | `./run/6502.sh` |
| BASIC-80 | [basic80.sh](run/basic80.sh) | `./run/basic80.sh` |
| N-BASIC | [nbasic.sh](run/nbasic.sh) | `./run/nbasic.sh` |
| N88-BASIC | [n88basic.sh](run/n88basic.sh) | `./run/n88basic.sh` |
| FM-7 F-BASIC | [fm7basic.sh](run/fm7basic.sh) | `./run/fm7basic.sh` |
| FM-11 F-BASIC | [fm11basic.sh](run/fm11basic.sh) | `./run/fm11basic.sh` |
| GW-BASIC | [gwbasic.sh](run/gwbasic.sh) | `./run/gwbasic.sh` |
| MSX-BASIC | [msxbasic.sh](run/msxbasic.sh) | `./run/msxbasic.sh` |
| QBasic | [qbasic.sh](run/qbasic.sh) | `./run/qbasic.sh` |
| Grant BASIC | [grantsbasic.sh](run/grantsbasic.sh) | `./run/grantsbasic.sh` |

## ファイルの実行

`./run/スクリプトファイル --file ファイル名` とすると、ファイルに書かれた行番号付き BASIC プログラムを読み込んだ状態で対話モードを起動します。この場合はまだ実行されないので、対話中に `RUN` を入力すれば実行できます。対話モードは `Ctrl-D` で終了します。QBasic は text shell に読み込んだプログラムを保持し、`RUN` のたびに real QBasic batch backend で実行します。

`./run/スクリプトファイル --run --file ファイル名` とすると、同じ読み込み済み状態からプログラムを実行し、プログラム終了後に対話モードも終了します。`PRINT` などの出力は標準出力に出力します。`INPUT` などプログラム実行中の入力要求は未対応で、未対応の実装では即エラー終了します。すでに対応している処理系はそのままです。

`--file` で与えるソースは ASCII だけで書かれている前提です。改行コードは `LF` / `CRLF` / `CR` を受理します。

共通の smoke test 用として、すべての runner で使う前提の [demo/sample.bas](demo/sample.bas) を置いてあります。

| BASIC | スクリプト | 実行方法 | 動作確認 |
| --- | --- | --- | --- |
| 6502 BASIC | [6502.sh](run/6502.sh) | `./run/6502.sh --run --file ファイル名` | `./run/6502.sh --run --file demo/6502.bas` |
| BASIC-80 | [basic80.sh](run/basic80.sh) | `./run/basic80.sh --run --file ファイル名` | `./run/basic80.sh --run --file demo/basic80.bas` |
| N-BASIC | [nbasic.sh](run/nbasic.sh) | `./run/nbasic.sh --run --file ファイル名` | `./run/nbasic.sh --run --file demo/nbasic.bas` |
| N88-BASIC | [n88basic.sh](run/n88basic.sh) | `./run/n88basic.sh --run --file ファイル名` | `./run/n88basic.sh --run --file demo/n88basic.bas` |
| FM-7 F-BASIC | [fm7basic.sh](run/fm7basic.sh) | `./run/fm7basic.sh --run --file ファイル名` | `./run/fm7basic.sh --run --file demo/fm7basic.bas` |
| FM-11 F-BASIC | [fm11basic.sh](run/fm11basic.sh) | `./run/fm11basic.sh --run --file ファイル名` | `./run/fm11basic.sh --run --file demo/fm11basic.bas` |
| GW-BASIC | [gwbasic.sh](run/gwbasic.sh) | `./run/gwbasic.sh --run --file ファイル名` | `./run/gwbasic.sh --run --file demo/gwbasic.bas` |
| MSX-BASIC | [msxbasic.sh](run/msxbasic.sh) | `./run/msxbasic.sh --run --file ファイル名` | `./run/msxbasic.sh --run --file demo/msxbasic.bas` |
| QBasic | [qbasic.sh](run/qbasic.sh) | `./run/qbasic.sh --run --file ファイル名` | `./run/qbasic.sh --run --file demo/qbasic.bas` |
| Grant BASIC | [grantsbasic.sh](run/grantsbasic.sh) | `./run/grantsbasic.sh --run --file ファイル名` | `./run/grantsbasic.sh --run --file demo/grantsbasic.bas` |

## ファイルを読み込んで対話（`-f` / `--file`）

`--file` オプションを使うと、行番号付き BASIC ソースをメモリに読み込んだ状態で対話端末を起動します（`RUN` はしません）。対話中に `RUN` を入力すれば実行できます。
`--run --file` を付けると、読み込み済みプログラムを実行し、プログラム終了後に対話モードも終了します。`--file` を省略すると通常の対話起動です。対話モードは `Ctrl-D` で終了します。`--file` で与えるソースは ASCII 前提で、改行コードは `LF` / `CRLF` / `CR` を受理します。実行時間は既定で無制限です。必要なときだけ `--timeout 90` や `--timeout 2m` のように指定してください。`--timeout` は batch/file-run 全体の wall time に効きます。

既定では、`INPUT` / `LINE INPUT` / `INPUT$` / `INKEY$` のようなプログラム実行中の入力要求は `--run --file` で未対応です。対応していない処理系では即エラー終了します。

| BASIC | 読み込んで対話 | 実行して終了 |
| --- | --- | --- |
| 6502 BASIC | `./run/6502.sh --file demo/6502.bas` | `./run/6502.sh --run --file demo/6502.bas` |
| BASIC-80 | `./run/basic80.sh --file demo/basic80.bas` | `./run/basic80.sh --run --file demo/basic80.bas` |
| N-BASIC | `./run/nbasic.sh --file demo/nbasic.bas` | `./run/nbasic.sh --run --file demo/nbasic.bas` |
| N88-BASIC | `./run/n88basic.sh --file demo/n88basic.bas` | `./run/n88basic.sh --run --file demo/n88basic.bas` |
| FM-7 F-BASIC | `./run/fm7basic.sh --file demo/fm7basic.bas` | `./run/fm7basic.sh --run --file demo/fm7basic.bas` |
| FM-11 F-BASIC | `./run/fm11basic.sh --file demo/fm11basic.bas` | `./run/fm11basic.sh --run --file demo/fm11basic.bas` |
| GW-BASIC | `./run/gwbasic.sh --file demo/gwbasic.bas` | `./run/gwbasic.sh --run --file demo/gwbasic.bas` |
| MSX-BASIC | `./run/msxbasic.sh --file demo/msxbasic.bas` | `./run/msxbasic.sh --run --file demo/msxbasic.bas` |
| QBasic | `./run/qbasic.sh --file demo/qbasic.bas` | `./run/qbasic.sh --run --file demo/qbasic.bas` |
| Grant BASIC | `./run/grantsbasic.sh --file demo/grantsbasic.bas` | `./run/grantsbasic.sh --run --file demo/grantsbasic.bas` |

実行経路、処理系ごとの差分、環境依存の注意点、忠実度メモは [details.md](details.md) を参照してください。
