from __future__ import annotations

import unittest

from dos_batch_filter import extract_clean_lines


class DosBatchFilterTests(unittest.TestCase):
    def test_extract_clean_lines_strips_qbasic_editor_noise(self) -> None:
        raw = (
            "Prepared QBasic runtime at /tmp/qbasic-dosemu\n"
            "10 B# = 4# * ATN(1)\x1b[4;2H20 T$ = MKD$(B#)\n"
            "\x1b[30m\x1b[47m   File  Edit  View  Search  Run  Debug  Options  Help\n"
            " 327  4272943  1360120\r\n"
            " 328  5419351  1725033\r\n"
            "FOUND  245850922 / 78256779\r\n"
            "TARGET\r\n"
            " 24  45  68  84  251  33  9  64\r\n"
            "ERROR: unknown window sizes li=0  co=0, setting to 80x25\n"
        )

        self.assertEqual(
            extract_clean_lines(raw),
            [
                "327 4272943 1360120",
                "328 5419351 1725033",
                "FOUND 245850922 / 78256779",
                "TARGET",
                "24 45 68 84 251 33 9 64",
            ],
        )

    def test_extract_clean_lines_strips_gwbasic_boot_noise(self) -> None:
        raw = (
            "Running dosemu2 with root privs is recommended with: dosemu -s\r\n"
            "FDPP kernel 1.7 [GIT: ] (compiled Feb 21 2026)\r\n"
            "\x1b[1;2H302  98313  31294\r\n"
            "\x1b[2;2H303  98668  31407\r\n"
            "\x1b[19;3H47  657408909  209259755  MATCH\r\n"
            "FOUND  657408909 / 209259755\r\n"
            "BYTES  194  104  33  162  218  15  73  130\r\n"
            "TARGET\r\n"
            " 194  104  33  162  218  15  73  130\r\n"
            "ERROR: KVM: error opening /dev/kvm: No such file or directory\r\n"
        )

        self.assertEqual(
            extract_clean_lines(raw),
            [
                "302 98313 31294",
                "303 98668 31407",
                "47 657408909 209259755 MATCH",
                "FOUND 657408909 / 209259755",
                "BYTES 194 104 33 162 218 15 73 130",
                "TARGET",
                "194 104 33 162 218 15 73 130",
            ],
        )

    def test_extract_clean_lines_preserves_mandelbrot_whitespace(self) -> None:
        raw = (
            "000111111111111111112222222233445C      643332222111110000000000000000000000000\r\n"
            "                                              864332221111111000000000000000000\r\n"
        )

        self.assertEqual(
            extract_clean_lines(raw),
            [
                "000111111111111111112222222233445C      643332222111110000000000000000000000000",
                "                                              864332221111111000000000000000000",
            ],
        )


if __name__ == "__main__":
    unittest.main()
