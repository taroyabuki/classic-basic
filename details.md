# Terminal Classic BASICs Details

GW-BASIC は `gwbasic FILENAME` でネイティブロード（`/RUN` なし）、QBasic は `qbasic FILENAME` で IDE にファイルを開きます。
N-BASIC・6502 BASIC は通常のファイル実行と同じソース注入経路を使いますが、末尾の `RUN` を送らずに対話端末へ移ります。BASIC-80 は MBASIC 起動後に `LOAD "RUNFILE.BAS"` を自動入力して同じ操作感に揃えています。

現状の実運用としては、対話入力より `--run --file PROGRAM.bas` を指定した file-run を優先してください。GW-BASIC / QBasic は対話で PTY / TTY 条件の影響を受けやすく、file-run も dosemu2 の実行条件に依存します。

`run/6502.sh` の既定 `--max-steps` は長めの数値探索でも止まりにくいよう `50000000` にしてあります。さらに長い probe を回すときは `--max-steps N` で上書きできます。

file-run の実行時間は既定で無制限です。必要なときだけ `--timeout` を付けてください。環境変数で指定する場合は、MSX-BASIC は `CLASSIC_BASIC_MSX_BATCH_TIMEOUT`、GW-BASIC / QBasic は `CLASSIC_BASIC_DOSEMU_BATCH_TIMEOUT`、BASIC-80 は `CLASSIC_BASIC_RUNCPM_BATCH_TIMEOUT` を使えます。値は `90`、`2.5`、`90s`、`2m` のように指定できます。

## 対話テストのやり方

GW-BASIC / QBasic の対話テストでは、**起動方法そのもの** が結果に影響します。特に dosemu2 系の runner は、stdin が TTY かどうかで挙動が変わります。

- `run/gwbasic.sh` と `run/qbasic.sh` は内部で `[[ -t 0 ]]` を見ており、stdin が TTY のときはまず `script(1)` による PTY ラップを試します。sandbox などで `script` が PTY を取れない場合は `expect` を使う fallback に自動で切り替わります
- `subprocess.run(..., stdin=PIPE)` や `subprocess.Popen(..., stdin=PIPE)` で起動すると stdin が TTY でなくなり、この PTY ラップ/対話 fallback は効きません
- その状態での `Syntax error` やキー化けは、**起動条件の違い** によって起きる可能性があります

正しい確認方法:

- GW-BASIC:
  - `expect -c 'spawn bash run/gwbasic.sh ...'`
  - または Python の `pty.openpty()` で slave FD を stdin/stdout/stderr に渡して spawn
- QBasic:
  - `expect -c 'spawn bash run/qbasic.sh ...'`
  - 起動時のダイアログは `ESC`、`Tab` + `Enter`、その後 `F6` で Immediate window に移る

運用上の推奨:

- まず `python3 -m pytest tests/test_fidelity_audit.py -q -k 'gwbasic_exec_smoke or qbasic_exec_smoke'` を通して smoke を確認する
- 実プログラム確認は、その後に `expect` か `pty.openpty()` 経路で行う
- `stdin=PIPE` の結果だけで GW-BASIC / QBasic の対話不具合と判断しない
- `codex` 上で確認する場合も、通常の対話セッションを前提にする。`--ask-for-approval never` のような特殊な approval / sandbox 条件は標準の再現環境とはみなさない。特に GW-BASIC / QBasic の対話確認には不適切で、file-run もその条件だけで可否判断しない。dosemu2 が `openpt failed Permission denied` や `Bad or missing Command Interpreter: E:\command.com` を出している場合は、まず実行環境側の制約を疑う

## 実行経路と忠実度

このリポジトリは「各 BASIC を同じ端末から実用的に触る」ことを優先しており、全系統が同じ意味で実機相当ではありません。特に `PROGRAM.bas` 実行時の経路が対話モードと異なるものがあります。

| BASIC | 対話エンジン | `PROGRAM.bas` 実行エンジン | 元の ROM / バイナリ | 既知の注意点 | byte-level 数値検証 |
| --- | --- | --- | --- | --- | --- |
| 6502 BASIC | `python3 -m z80_basic.osi_basic` | 同じ | `third_party/msbasic/osi.bin` を custom 6502 emulator (`py65`) で実行 | 完全な OSI 実機エミュレーションではなく、コンソールと monitor call は hook ベース | ROM 内部の数値系確認は可。実機全体の byte-level 再現とは別 |
| BASIC-80 | RunCPM 上の `MBASIC.COM` | 同じ | 実バイナリ `MBASIC.COM`。この repo の既定 asset は起動 banner 上 `BASIC-85 Rev. 5.29 [CP/M Version]` | RunCPM は完全な CP/M / Z80 実機再現を目標にしていない | MBASIC の数値 probe には使える。表記は「MBASIC on RunCPM」が正確 |
| N-BASIC | `python3 -m pc8001_terminal` + `downloads/pc8001/N80_11.rom` | 同じ | ROM を `pc8001_terminal` scaffold で実行 | scaffold は少数の hook で行入力・キー出力のみ橋渡し。完全な PC-8001 エミュレータではない | ROM の数値検証に使える |
| GW-BASIC | dosemu2 上の `GWBASIC.EXE` | 同じ | 実バイナリ `GWBASIC.EXE`。setup は `gwbasic-3.23.7z` から展開 | batch は `-ks < /dev/null` で実行。`PRINT` 出力は CBATCH.TXT 経由、`OPEN "OUT.TXT"` 出力は out.txt 経由で取得 | 実バイナリ経路。wrapper の実行信頼性は環境依存 |
| MSX-BASIC | openMSX + MSX ROM / machine | 同じ | openMSX 上で ROM を実行。既定 ROM は `setup/msxbasic.sh` が SHA-256 固定で抽出 | BASIC 実行自体は ROM/openMSX 側だが、batch は typed input と screen scrape に依存 | ROM 数値経路の確認は可。batch 自動化は近似的 |
| QBasic | dosemu2 上の `QBASIC.EXE` | 同じ | 実バイナリ `QBASIC.EXE`。setup は `qbasic-1.1.zip` から展開 | batch は `-ks < /dev/null` + BOOT.BAS/RUNFILE.BAS ステージングで実行。`OPEN "OUT.TXT"` 出力は out.txt 経由で取得 | 実バイナリ経路。wrapper の実行信頼性は環境依存 |

### 出自メモ

- `6502 BASIC` は Microsoft 6502 BASIC 系ソース由来ですが、この repo で直接使っているのは `third_party/msbasic/osi.bin` という vendored ROM/binary と custom 6502 emulator です。ここでの表現は「MS 由来の ROM/binary を custom emulator で動かす」が正確です。
- `6502 BASIC` については Microsoft が `microsoft/BASIC-M6502` として公開しているソース tree があります。ただし、この repo が直接使っているのはその GitHub repo そのものではなく、vendored `osi.bin` です。
- `GW-BASIC` と `QBasic` は Microsoft 製の実行ファイルです。ただし、この repo が前提にしているのは手元 asset / archive から展開した `GWBASIC.EXE` / `QBASIC.EXE` であり、「Microsoft が現在公式配布している公開物」という意味では書いていません。
- `GW-BASIC` については Microsoft が `microsoft/GW-BASIC` として公開しているソース tree があります。ただし、この repo が直接使うのはそのソース tree ではなく、archive から展開した `GWBASIC.EXE` です。
- `BASIC-80` も Microsoft 系ですが、この repo の既定 asset は banner 上 `BASIC-85 Rev. 5.29 [CP/M Version]` を名乗る `MBASIC.COM` です。
- `QBasic` については、この repo では archive から展開した `QBASIC.EXE` を前提にしています。

### 監査結果

#### 6502 BASIC

- `third_party/msbasic/README.md` は、この tree が既知の 6502 Microsoft BASIC を生成し、元ファイルと byte-by-byte 比較すると説明しています。したがって `osi.bin` は独自互換実装ではなく、元 ROM と一致することを目標にした vendored binary / rebuilt artifact と扱えます。
- `src/z80_basic/osi_basic.py` で hook している monitor call は `OSI_MONCOUT`, `OSI_MONRDKEY`, `OSI_MONISCNTC`, `OSI_LOAD`, `OSI_SAVE` だけです。`LOAD` と `SAVE` は未実装ですが、算術そのものは ROM 内部で走ります。
- ローカル確認:
  - `./run/6502.sh --exec 'PRINT 2+2'` -> `4`
  - 専用の数値 check probe で `130 73 15 219` と 2 本の差分 `0` を確認
  - 専用の探索 probe で `FOUND 1` を確認
- 結論:
  - 「custom 6502 emulator + OSI BASIC ROM/binary」
  - 実機全体の再現ではない
  - ただし ROM の数値挙動確認には実用的
  - 「Microsoft が現在公開している公式配布物」ではなく、vendored ROM/binary artifact と書くのが安全

#### BASIC-80

- `run/basic80.sh` は `MBASIC.COM` を RunCPM に載せるだけで、言語本体は MBASIC の実バイナリです。
- `third_party/RunCPM/readme.md` は、RunCPM の目的が「perfect Z80 CP/M computer emulation ではない」と明記しています。
- 既定の `downloads/MBASIC.COM` は起動時に `BASIC-85 Rev. 5.29 [CP/M Version]` と名乗ります。したがって、この repo 名の `basic80` は汎称で、実際に動く既定 asset は MBASIC 5.29 です。
- ローカル確認:
  - 専用の数値 check probe で `194 104 33 162 218 15 73 130` と差分 `0` を確認
  - 専用の探索 probe で `FOUND 2` を確認
- 結論:
  - 「real MBASIC binary on RunCPM」
  - 記述上は単に `BASIC-80` ではなく「MBASIC on RunCPM」または「MBASIC 5.29 on RunCPM」と書く方が正確
  - 数値差が出る場合、通常の算術ではまず MBASIC 側の型や丸めを疑うべきで、RunCPM は CP/M 環境差として別扱いするのが妥当

#### N-BASIC

### N-BASIC 補足

`run/nbasic.sh` は PC-8001 ROM scaffold（`pc8001_terminal`）のみを使います。

- `./run/nbasic.sh` は PC-8001 ROM scaffold を起動します。
- `./run/nbasic.sh --file PROGRAM.bas` はプログラムを読み込んで対話モードに入ります。
- `./run/nbasic.sh --run --file PROGRAM.bas` は PC-8001 ROM batch route でプログラムを実行します。
- `./run/nbasic.sh [pc8001-terminal-options]` はオプションをそのまま `pc8001_terminal` に渡します。

現在の ROM scaffold で `downloads/pc8001/N80_11.rom` を起動すると、次の再現が取れます。

```sh
PYTHONPATH=src python3 -m pc8001_terminal --keys 'PRINT 12\rDEFDBL A-Z\rA#=1\r'
```

出力（対話モード・`--keys` 使用時はバナーと `Ok` がそのまま表示されます）:

```text
NEC PC-8001 BASIC Ver 1.1
Copyright 1979 (C) by Microsoft
Ok
 12 
Ok
Ok
Ok
```

`./run/nbasic.sh --run --file PROGRAM.bas` でファイルを実行すると、バナーと `Ok` プロンプトは自動的に除去されます。また、`PRINT` で数値を表示した場合は非負数の前後にスペースが付きます（例: ` 3.14 `）。

この ROM route は `pc8001_terminal` 内の少数の hard-coded hook で行入力・キー入力・1文字出力だけを橋渡ししており、完全な PC-8001 エミュレータではありません。

ローカル確認:

- `./run/nbasic.sh --run --file demo/nbasic.bas`
- 専用の数値 check probe で `194 104 33 162 218 15 73 130` と差分 `0` を確認
- 専用の探索 probe で `FOUND 2` を確認

#### MSX-BASIC

- `run/msxbasic.sh` は `python3 -m msx_basic.cli` を起動し、`src/msx_basic/bridge.py` 経由で openMSX の control protocol を使います。
- BASIC の算術は repo 内で再実装していません。repo 側がやっているのは「ROM を載せた openMSX へキー入力を流す」「画面とメモリを読む」だけです。
- `setup/msxbasic.sh` は `TARGET_ROM_SHA256=1c85dac5536fa3ba6f2cb70deba02ff680b34ac6cc787d2977258bd663a99555` の 32 KB BIOS+BASIC ROM を抽出して `downloads/msx/msx1-basic-bios.rom` に置くので、既定 ROM は固定されています。
- ローカル確認:
  - `./run/msxbasic.sh --run --file demo/msxbasic.bas` -> `HELLO, WORLD`, `INTEGER 14`, `PI 3.1415926535898`
  - 専用の数値 check probe で BCD byte dump と 3 本の差分すべて `0` を確認
  - 専用の探索 probe で `FOUND 2` を確認
  - 長時間の数値 probe は完走に 60s 超かかる場合があるため、必要なら `--timeout 120` か `CLASSIC_BASIC_MSX_BATCH_TIMEOUT=120` を付けて実行
- 結論:
  - 「openMSX-backed real ROM execution」
  - 数値実行は ROM 側を見てよい
  - ただし batch 自動化は typed input と screen scrape の都合で近似的
  - PTY 対話は `MSX-BASIC ready.` までは見えても、その後の `PRINT 4*ATN(1)` 出力 capture は不安定

#### GW-BASIC

- `run/gwbasic.sh` の interactive と file-run はどちらも同じ `GWBASIC.EXE` を dosemu2 に渡します。差は wrapper が `-E "gwbasic"` を使うか、`-E "gwbasic RUNFILE.BAS > CBATCH.TXT"` を使うかだけです。
- `setup/gwbasic.sh` / `scripts/gwbasic-common.sh` の既定 asset は `downloads/gwbasic/gwbasic-3.23.7z` から抽出される `GWBASIC.EXE` です。
- file-run は、dosemu2 が正常に DOS を起動でき、EMUFS が機能している環境では `CBATCH.TXT` / `out.txt` の両経路で結果取得できます。
- 対話確認では、呼び出し元端末の設定が dosemu2 の `-kt`（keyboard from TTY）モードに影響するため、クリーンな PTY を確保した起動方法を使ってください。
- **テストの注意点**:
  - 自動テストでは `expect` で `run/gwbasic.sh` を spawn する形を使うこと（`expect` が TTY を確立するため、`exec_dosemu_in_pty` ラップが正しく動く）
  - `subprocess.run(["bash", "run/gwbasic.sh"])` のように pipe 接続すると `stdin` が TTY でなくなり、`script` ラップが起きない
  - `test_gwbasic_exec_smoke` がこの経路を自動検証しているため、テスト suite を通してから対話動作の可否を判断すること
- 結論:
  - 「real GW-BASIC under dosemu2」
  - interpreter fidelity と wrapper reliability は分けて考える
  - 実機寄りの記述としては「IBM PC hardware emulation」ではなく「GW-BASIC executable on dosemu2」
  - これは Microsoft 製バイナリだが、この repo では current official distribution とは主張しない

#### QBasic

- `run/qbasic.sh` の interactive と file-run はどちらも同じ `QBASIC.EXE` を dosemu2 に渡します。差は wrapper が通常起動するか、`/RUN` と `BOOT.BAS` を使って file-run するかだけです。
- `setup/qbasic.sh` / `scripts/qbasic-common.sh` の既定 asset は `downloads/qbasic/qbasic-1.1.zip` から抽出される `QBASIC.EXE` です。
- file-run は、dosemu2 が正常に DOS を起動でき、EMUFS と `BOOT.BAS` / `RUNFILE.BAS` の staging が成立する環境で通ります。
- 対話確認では、GW-BASIC と同様に dosemu2 の `-kt` モードが呼び出し元端末の状態に影響されます。さらに起動直後のダイアログを閉じて Immediate window に移る手順が必要です。
- **テストの注意点**:
  - GW-BASIC と同様に、自動テストでは `expect` で spawn する形を使うこと
  - QBasic は起動時に 2 つのダイアログが順番に出る。手動操作では ESC → Tab+CR → F6 の順で閉じてから Immediate window に入力する
  - `test_qbasic_exec_smoke` がこの経路を自動検証しているため、テスト suite を通してから対話動作の可否を判断すること
- 結論:
  - 「real QBasic under dosemu2」
  - interpreter fidelity と wrapper reliability は分けて考える
  - 実機寄りの記述としては「IBM PC hardware emulation」ではなく「QBasic executable on dosemu2」
  - これは Microsoft 製バイナリだが、この repo では current official distribution とは主張しない

### 記述方針

- 6502 BASIC は「OSI BASIC on custom 6502 emulator」
- BASIC-80 は「MBASIC 5.29 on RunCPM」
- N-BASIC は「ROM scaffold が唯一の backend」。batch mode (file-run) は安定動作。PTY 対話は sandbox / TTY 条件の影響を受ける場合がある
- MSX-BASIC は「openMSX + pinned BIOS/BASIC ROM」
- GW-BASIC / QBasic は「real executable under dosemu2」。特殊 sandbox 条件では batch も含めて dosemu2 起動条件に左右される。wrapper 制約は別注記する
