# Terminal Classic BASICs

通常のターミナル上で古典的な Microsoft BASIC 系を動かすための実用構成です。

冒頭の整理として、このリポジトリで扱っている BASIC は次のとおりです。

| BASIC | 年代 | 代表的な対象コンピュータ | 浮動小数点数の規格 |
| --- | --- | --- | --- |
| 6502 BASIC | 1977 | Ohio Scientific C1P / Challenger 1P 系 | Microsoft 6502 BASIC small FP (`CONFIG_SMALL`) |
| BASIC-80 | 1977-1980 | 汎用 CP/M-80 マシン | Microsoft Binary Format (MBF) |
| N-BASIC | 1979 | NEC PC-8001 | Microsoft Binary Format (MBF) |
| GW-BASIC | 1983 | IBM PC 互換機 | Microsoft Binary Format (MBF) |
| MSX-BASIC | 1983-1988 | MSX / MSX2 / MSX2+ 系 | BCD (packed BCD, 底 10) |
| QBasic | 1991 | MS-DOS 5 世代の IBM PC 互換機 | IEEE 754 binary64 |

## セットアップ

| BASIC | スクリプト | 実行方法 |
| --- | --- | --- |
| 6502 BASIC | [6502.sh](setup/6502.sh) | `./setup/6502.sh` |
| BASIC-80 | [basic80.sh](setup/basic80.sh) | `./setup/basic80.sh` |
| N-BASIC | [nbasic.sh](setup/nbasic.sh) | `./setup/nbasic.sh` |
| GW-BASIC | [gwbasic.sh](setup/gwbasic.sh) | `./setup/gwbasic.sh` |
| MSX-BASIC | [msxbasic.sh](setup/msxbasic.sh) | `./setup/msxbasic.sh` |
| QBasic | [qbasic.sh](setup/qbasic.sh) | `./setup/qbasic.sh` |

## 対話的な利用

| BASIC | スクリプト | 実行方法 |
| --- | --- | --- |
| 6502 BASIC | [6502.sh](run/6502.sh) | `./run/6502.sh` |
| BASIC-80 | [basic80.sh](run/basic80.sh) | `./run/basic80.sh` |
| N-BASIC | [nbasic.sh](run/nbasic.sh) | `./run/nbasic.sh` |
| GW-BASIC | [gwbasic.sh](run/gwbasic.sh) | `./run/gwbasic.sh` |
| MSX-BASIC | [msxbasic.sh](run/msxbasic.sh) | `./run/msxbasic.sh` |
| QBasic | [qbasic.sh](run/qbasic.sh) | `./run/qbasic.sh` |

## ファイルの実行

| BASIC | スクリプト | 実行方法 | 動作確認 |
| --- | --- | --- | --- |
| 6502 BASIC | [6502.sh](run/6502.sh) | `./run/6502.sh --run --file ファイル名` | `./run/6502.sh --run --file demo/6502.bas` |
| BASIC-80 | [basic80.sh](run/basic80.sh) | `./run/basic80.sh --run --file ファイル名` | `./run/basic80.sh --run --file demo/basic80.bas` |
| N-BASIC | [nbasic.sh](run/nbasic.sh) | `./run/nbasic.sh --run --file ファイル名` | `./run/nbasic.sh --run --file demo/nbasic.bas` |
| GW-BASIC | [gwbasic.sh](run/gwbasic.sh) | `./run/gwbasic.sh --run --file ファイル名` | `./run/gwbasic.sh --run --file demo/gwbasic.bas` |
| MSX-BASIC | [msxbasic.sh](run/msxbasic.sh) | `./run/msxbasic.sh --run --file ファイル名` | `./run/msxbasic.sh --run --file demo/msxbasic.bas` |
| QBasic | [qbasic.sh](run/qbasic.sh) | `./run/qbasic.sh --run --file ファイル名` | `./run/qbasic.sh --run --file demo/qbasic.bas` |

## ファイルを読み込んで対話（`-f` / `--file`）

`--file` オプションを使うと、ファイルをメモリに読み込んだ状態で対話端末を起動します（`RUN` はしません）。
`--run --file` を付けると非対話で実行して終了します。`--file` を省略すると通常の対話起動です。実行時間は既定で無制限です。必要なときだけ `--timeout 90` や `--timeout 2m` のように指定してください。

Codex 上では、`codex --ask-for-approval never` のような特殊な approval / sandbox 条件を標準の再現環境とはみなさないでください。特に GW-BASIC / QBasic は対話確認では PTY / TTY 条件が重要で、file-run も sandbox や dosemu2 の実行条件に影響されます。`openpt failed Permission denied` や `Bad or missing Command Interpreter: E:\command.com` のような dosemu2 起動失敗が出ている条件では、その結果だけで挙動を判断しないでください。

| BASIC | 読み込んで対話 | 実行して終了 |
| --- | --- | --- |
| 6502 BASIC | `./run/6502.sh --file demo/6502.bas` | `./run/6502.sh --run --file demo/6502.bas` |
| BASIC-80 | `./run/basic80.sh --file demo/basic80.bas` | `./run/basic80.sh --run --file demo/basic80.bas` |
| N-BASIC | `./run/nbasic.sh --file demo/nbasic.bas` | `./run/nbasic.sh --run --file demo/nbasic.bas` |
| GW-BASIC | `./run/gwbasic.sh --file demo/gwbasic.bas` | `./run/gwbasic.sh --run --file demo/gwbasic.bas` |
| MSX-BASIC | `./run/msxbasic.sh --file demo/msxbasic.bas` | `./run/msxbasic.sh --run --file demo/msxbasic.bas` |
| QBasic | `./run/qbasic.sh --file demo/qbasic.bas` | `./run/qbasic.sh --run --file demo/qbasic.bas` |

詳細な実行経路、タイムアウト、対話テスト、忠実度メモは [details.md](details.md) を参照してください。
