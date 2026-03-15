from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


class DosemuNamedSemShimTests(unittest.TestCase):
    def test_shim_makes_sem_open_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            shim_path = temp_path / "dosemu_named_sem_shim.so"

            subprocess.run(
                [
                    "cc",
                    "-shared",
                    "-fPIC",
                    "-O2",
                    "-pthread",
                    "-o",
                    str(shim_path),
                    str(ROOT_DIR / "src/dosemu_named_sem_shim.c"),
                ],
                cwd=ROOT_DIR,
                check=True,
                timeout=20,
            )

            env = os.environ.copy()
            env["LD_PRELOAD"] = str(shim_path)
            result = subprocess.run(
                [
                    "python3",
                    "-c",
                    textwrap.dedent(
                        """
                        import ctypes
                        import os

                        libc = ctypes.CDLL(None, use_errno=True)
                        libc.sem_open.restype = ctypes.c_void_p
                        libc.sem_open.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
                        libc.sem_wait.argtypes = [ctypes.c_void_p]
                        libc.sem_post.argtypes = [ctypes.c_void_p]
                        libc.sem_close.argtypes = [ctypes.c_void_p]
                        libc.sem_unlink.argtypes = [ctypes.c_char_p]

                        name = f"/shim_test_{os.getpid()}".encode("ascii")
                        sem = libc.sem_open(name, os.O_CREAT | os.O_EXCL, 0o600, 1)
                        if not sem or sem == ctypes.c_void_p(-1).value:
                            raise SystemExit(2)
                        if libc.sem_wait(ctypes.c_void_p(sem)) != 0:
                            raise SystemExit(3)
                        if libc.sem_post(ctypes.c_void_p(sem)) != 0:
                            raise SystemExit(4)
                        if libc.sem_close(ctypes.c_void_p(sem)) != 0:
                            raise SystemExit(5)
                        if libc.sem_unlink(name) != 0:
                            raise SystemExit(6)
                        """
                    ),
                ],
                cwd=ROOT_DIR,
                env=env,
                timeout=20,
            )

            self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
