# Mandelbrot

Source:
- https://www.retrobrewcomputers.org/forum/index.php?goto=4704&t=msg&th=201
- https://haserin09.la.coocan.jp/asciiart.html

確認できた処理系はすべて同じ結果になりました。QBasic と GW-BASIC は元の全文出力が一致し、N-BASIC、BASIC-80、6502 BASIC、FM7-BASIC、MSX-BASIC は検証版の `SPACE OK / HITS OK / SUM1 OK / SUM2 OK` が一致しています。

```bash
./run/qbasic.sh --run --file demo/mandelbrot/asciiart.bas
./run/gwbasic.sh --run --file demo/mandelbrot/asciiart.bas
./run/basic80.sh --run --file demo/mandelbrot/summary.bas
./run/6502.sh --run --file demo/mandelbrot/asciiart.bas
./run/nbasic.sh --run --file demo/mandelbrot/asciiart-nbasic.bas
./run/fm7basic.sh --run --file demo/mandelbrot/verify-fm7.bas
./run/msxbasic.sh --run --file demo/mandelbrot/verify-msx-simple.bas

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

verify 出力:

```text
SPACE OK
HITS OK
SUM1 OK
SUM2 OK
```

Notes:
- N-BASIC は投稿コードそのままの `CHR$(48+I)` で `Subscript out of range` になるので、比較には [asciiart-nbasic.bas](/root/work/classic-basic/demo/mandelbrot/asciiart-nbasic.bas) を使う
- FM7-BASIC は [verify-fm7.bas](/root/work/classic-basic/demo/mandelbrot/verify-fm7.bas)、MSX-BASIC は [verify-msx-simple.bas](/root/work/classic-basic/demo/mandelbrot/verify-msx-simple.bas) を使う
