"""ブート完了テスト

各 BASIC マシンのブートシーケンスが

  1. 合理的なステップ数以内に完了する（input_wait またはプロンプト到達）
  2. ブート中に何らかのコンソール出力を生成する

ことを検証するテスト。

「ブートループ」バグ（マシンが初期化サイクルを繰り返すだけでユーザー向き
プロンプトに到達しない）の回帰テストとして機能する。

ROM ファイルが必要。存在しない場合はスキップ。
"""

from __future__ import annotations

import unittest
from pathlib import Path

# --------------------------------------------------------------------------- #
# 共通定数
# --------------------------------------------------------------------------- #

_REPO = Path(__file__).resolve().parent.parent

# ブート完了の許容ステップ数（1 ラウンドあたり）
# 正常動作するエミュレータはこれより少ないステップで完了する。
# ブートループが起きている場合はこのステップを使い切って失敗する。
_BOOT_MAX_STEPS = 500_000
_BOOT_MAX_ROUNDS = 10  # ≤ 5M ステップ合計


# --------------------------------------------------------------------------- #
# PC-8001 (N-BASIC)
# --------------------------------------------------------------------------- #

_N80_ROM = _REPO / "downloads/pc8001/N80_11.rom"


class PC8001BootTests(unittest.TestCase):
    """pc8001_terminal のブート完了を検証する（比較対象）。"""

    def _make_machine(self):
        from pc8001_terminal.machine import PC8001Config, PC8001Machine, RomSpec

        return PC8001Machine(
            PC8001Config(
                roms=(RomSpec(path=_N80_ROM, start=0x0000, name="N80_11.rom"),),
                entry_point=0x0000,
                max_steps=_BOOT_MAX_STEPS,
                batch_rounds=_BOOT_MAX_ROUNDS,
            )
        )

    def test_boot_reaches_input_wait(self) -> None:
        """ブートが入力待ちに到達する。"""
        if not _N80_ROM.is_file():
            self.skipTest(f"ROM not available: {_N80_ROM}")

        machine = self._make_machine()
        machine.load_roms()
        machine.inject_keys([ord("0"), 0x0D])  # "How many files?" 相当への回答

        final_reason = "step_limit"
        for _ in range(_BOOT_MAX_ROUNDS):
            result = machine.run_firmware(max_steps=_BOOT_MAX_STEPS)
            if result.reason != "step_limit":
                final_reason = result.reason
                break

        self.assertEqual(
            final_reason,
            "input_wait",
            f"ブートが input_wait に到達しなかった (reason={final_reason!r})。",
        )

    def test_boot_produces_console_output(self) -> None:
        """ブート中にコンソール出力が生成される。"""
        if not _N80_ROM.is_file():
            self.skipTest(f"ROM not available: {_N80_ROM}")

        machine = self._make_machine()
        machine.load_roms()
        machine.inject_keys([ord("0"), 0x0D])

        for _ in range(_BOOT_MAX_ROUNDS):
            result = machine.run_firmware(max_steps=_BOOT_MAX_STEPS)
            if result.reason != "step_limit":
                break

        text = machine.consume_console_output()
        self.assertGreater(len(text), 0, "ブート中にコンソール出力がゼロ。")


# --------------------------------------------------------------------------- #
# Grant Searle Simple Z80 BASIC
# --------------------------------------------------------------------------- #

_GRANTS_ROM = _REPO / "downloads/grants-basic/rom.bin"


class GrantsBasicBootTests(unittest.TestCase):
    """grants_basic のブート完了を検証する（比較対象）。"""

    def test_boot_produces_memory_top_prompt(self) -> None:
        """ブートが "Memory top?" プロンプトを出力する。"""
        if not _GRANTS_ROM.is_file():
            self.skipTest(f"ROM not available: {_GRANTS_ROM}")

        from grants_basic.machine import GrantSearleConfig, GrantSearleMachine

        machine = GrantSearleMachine(
            GrantSearleConfig(
                rom_path=_GRANTS_ROM,
                boot_step_budget=_BOOT_MAX_STEPS * _BOOT_MAX_ROUNDS,
                prompt_step_budget=_BOOT_MAX_STEPS * _BOOT_MAX_ROUNDS,
            )
        )
        machine.load_rom()

        # "Memory top?" が出るまで実行
        total = 0
        limit = _BOOT_MAX_STEPS * _BOOT_MAX_ROUNDS
        while "Memory top?" not in machine.console_text() and total < limit:
            result = machine.run_slice(_BOOT_MAX_STEPS)
            total += result.steps
            if result.reason not in ("step_limit", "halted"):
                break

        self.assertIn(
            "Memory top?",
            machine.console_text(),
            f"ブートが 'Memory top?' プロンプトを出力しなかった "
            f"({total} ステップ実行)。",
        )


if __name__ == "__main__":
    unittest.main()
