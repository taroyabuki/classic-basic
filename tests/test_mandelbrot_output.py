from __future__ import annotations

import unittest

from mandelbrot_output import (
    extract_any_mandelbrot_block,
    extract_mandelbrot_art_block,
    extract_wrapped_mandelbrot_block,
    has_mandelbrot_art_fragments,
    reconstruct_plain_segmented_mandelbrot_lines,
    reconstruct_wrapped_mandelbrot_lines,
    reconstruct_segmented_mandelbrot_lines,
)


class MandelbrotOutputTests(unittest.TestCase):
    def test_extract_mandelbrot_art_block_returns_latest_25_line_window(self) -> None:
        art = [
            "000000011111111111111111122222233347E7AB322222111100000000000000000000000000000",
        ] * 25

        self.assertEqual(extract_mandelbrot_art_block(["noise", *art, "Ready"]), art)

    def test_reconstruct_segmented_mandelbrot_lines_rejoins_three_part_rows(self) -> None:
        first = "000000011111111111111111122222233347E7AB322222111100000000000000000000000000000"
        second = "000001111111111111111122222222333557BF75433222211111000000000000000000000000000"
        lines = [
            "1" + first[:37] + "|",
            "2" + first[37:74] + "|",
            "3" + first[74:] + "|",
            "1" + second[:37] + "|",
            "2" + second[37:74] + "|",
            "3" + second[74:] + "|",
        ] + [
            "1" + first[:37] + "|",
            "2" + first[37:74] + "|",
            "3" + first[74:] + "|",
        ] * 23

        block = reconstruct_segmented_mandelbrot_lines(lines)

        self.assertEqual(len(block), 25)
        self.assertIn(second, block)
        self.assertEqual(block[-1], first)

    def test_extract_any_mandelbrot_block_prefers_direct_art(self) -> None:
        art = [
            "000000011111111111111111122222233347E7AB322222111100000000000000000000000000000",
        ] * 25
        lines = ["1" + art[0][:37] + "|", *art]

        self.assertEqual(extract_any_mandelbrot_block(lines), art)

    def test_reconstruct_plain_segmented_mandelbrot_lines_rejoins_three_part_rows(self) -> None:
        line = "000000011111111111111111122222233347E7AB322222111100000000000000000000000000000"
        parts = [line[:37], line[37:74], line[74:]]

        self.assertEqual(
            reconstruct_plain_segmented_mandelbrot_lines(parts * 25),
            [line] * 25,
        )

    def test_extract_any_mandelbrot_block_dedents_small_common_gutter(self) -> None:
        line = "000000011111111111111111122222233347E7AB322222111100000000000000000000000000000"
        parts = ["  " + line[:37], "  " + line[37:74], "  " + line[74:]]

        self.assertEqual(extract_any_mandelbrot_block(parts * 25), [line] * 25)

    def test_reconstruct_plain_segmented_mandelbrot_lines_accepts_missing_blank_first_segment(self) -> None:
        line = "                                              864332221111111000000000000000000"
        parts = [line[37:74], line[74:]]

        self.assertEqual(
            reconstruct_plain_segmented_mandelbrot_lines(parts * 25),
            [line] * 25,
        )

    def test_reconstruct_wrapped_mandelbrot_lines_rejoins_40_column_wrap(self) -> None:
        line = "000111111111111111112222222233445C      643332222111110000000000000000000000000"

        self.assertEqual(
            reconstruct_wrapped_mandelbrot_lines([line[:40].rstrip(), line[40:].rstrip()]),
            [line],
        )

    def test_extract_wrapped_mandelbrot_block_accepts_blank_left_half_rows(self) -> None:
        line = "                                              864332221111111000000000000000000"
        wrapped_lines = [line[40:].rstrip()] * 25

        self.assertEqual(extract_wrapped_mandelbrot_block(wrapped_lines), [line] * 25)

    def test_has_mandelbrot_art_fragments_detects_split_and_direct_forms(self) -> None:
        self.assertTrue(
            has_mandelbrot_art_fragments(
                ["1" + "000000011111111111111111122222233347E" + "|"]
            )
        )
        self.assertTrue(
            has_mandelbrot_art_fragments(
                ["000000011111111111111111122222233347E7AB322222111100000000000000000000000000000"]
            )
        )
        self.assertTrue(
            has_mandelbrot_art_fragments(
                ["000000011111111111111111122222233347E7AB", "322222111100000000000000000000000000000"]
            )
        )


if __name__ == "__main__":
    unittest.main()
