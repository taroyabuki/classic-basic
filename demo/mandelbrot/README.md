# Mandelbrot

Source:
- https://www.retrobrewcomputers.org/forum/index.php?goto=4704&t=msg&th=201
- https://haserin09.la.coocan.jp/asciiart.html

確認できた処理系は、runner の `stdout` で同じ 25 行 x 79 桁の全文字アートに揃えます。オリジナルの前提に合わせ、`WIDTH 80` は描画前に効いているものとして扱います。ただし方言差があるので、共通ソース自体には `WIDTH` 文を埋め込まず、必要な runner 側で前処理します。wide 版は [asciiart.bas](/root/work/classic-basic/demo/mandelbrot/asciiart.bas)、FM-7 版は [asciiart-fm7.bas](/root/work/classic-basic/demo/mandelbrot/asciiart-fm7.bas)、N-BASIC 版は [asciiart-nbasic.bas](/root/work/classic-basic/demo/mandelbrot/asciiart-nbasic.bas) を使います。

```bash
./run/qbasic.sh --run --file demo/mandelbrot/asciiart.bas
./run/gwbasic.sh --run --file demo/mandelbrot/asciiart.bas
./run/basic80.sh --run --file demo/mandelbrot/asciiart.bas
./run/6502.sh --run --file demo/mandelbrot/asciiart.bas
./run/nbasic.sh --run --file demo/mandelbrot/asciiart-nbasic.bas
./run/n88basic.sh --run --file demo/mandelbrot/asciiart.bas
./run/fm7basic.sh --run --file demo/mandelbrot/asciiart-fm7.bas
./run/fm11basic.sh --run --file demo/mandelbrot/asciiart.bas
./run/msxbasic.sh --run --file demo/mandelbrot/asciiart.bas

python3 demo/mandelbrot/compare_output.py \
  demo/mandelbrot/expected.txt \
  demo/mandelbrot/output/qbasic.norm.txt
```

Full Output:

`QBasic` / `GW-BASIC` reference output:

```text
000000011111111111111111122222233347E7AB322222111100000000000000000000000000000
000001111111111111111122222222333557BF75433222211111000000000000000000000000000
000111111111111111112222222233445C      643332222111110000000000000000000000000
011111111111111111222222233444556C      654433332211111100000000000000000000000
11111111111111112222233346 D978 BCF    DF9 6556F4221111110000000000000000000000
111111111111122223333334469                 D   6322111111000000000000000000000
1111111111222333333334457DB                    85332111111100000000000000000000
11111122234B744444455556A                      96532211111110000000000000000000
122222233347BAA7AB776679                         A32211111110000000000000000000
2222233334567        9A                         A532221111111000000000000000000
222333346679                                    9432221111111000000000000000000
234445568  F                                   B5432221111111000000000000000000
                                              864332221111111000000000000000000
234445568  F                                   B5432221111111000000000000000000
222333346679                                    9432221111111000000000000000000
2222233334567        9A                         A532221111111000000000000000000
122222233347BAA7AB776679                         A32211111110000000000000000000
11111122234B744444455556A                      96532211111110000000000000000000
1111111111222333333334457DB                    85332111111100000000000000000000
111111111111122223333334469                 D   6322111111000000000000000000000
11111111111111112222233346 D978 BCF    DF9 6556F4221111110000000000000000000000
011111111111111111222222233444556C      654433332211111100000000000000000000000
000111111111111111112222222233445C      643332222111110000000000000000000000000
000001111111111111111122222222333557BF75433222211111000000000000000000000000000
000000011111111111111111122222233347E7AB322222111100000000000000000000000000000
```

Notes:
- オリジナルの仕様に合わせ、`WIDTH 80` は描画前に済んでいる前提にしている。`6502` / `BASIC-80` など `WIDTH` 文を受けない系統があるため、共通ソースには直書きしない
- N-BASIC は投稿コードそのままの `CHR$(48+I)` で `Subscript out of range` になるので、[asciiart-nbasic.bas](/root/work/classic-basic/demo/mandelbrot/asciiart-nbasic.bas) では明示マッピングを使う
- FM-7 は string space が厳しいので、[asciiart-fm7.bas](/root/work/classic-basic/demo/mandelbrot/asciiart-fm7.bas) で `CLEAR 5000` と上下対称の mirror 出力を使う
- FM7-BASIC / MSX-BASIC の `mandelbrot` batch 実行では runner が `WIDTH 80` を前置してから `RUN` する
- [asciiart-40col.bas](/root/work/classic-basic/demo/mandelbrot/asciiart-40col.bas) は旧 40 桁分割の補助資産として残してある
- `summary.bas`, `compare.bas`, `verify-*.bas` は診断・デバッグ用として残している
