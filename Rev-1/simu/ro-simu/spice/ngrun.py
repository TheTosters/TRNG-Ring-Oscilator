#!/usr/bin/env python3
"""Batch ngspice runner na libngspice.so (w systemie nie ma CLI `ngspice`,
jest tylko biblioteka wspoldzielona uzywana przez KiCad).

Uzycie:
    python3 ngrun.py plik.cir [...]

Netlist powinien zawierac sekcje .control/.endc ktora sama zapisuje wyniki
(wrdata / print / meas). Wyjscie ngspice leci na stdout.
"""
import ctypes
import os
import sys

LIB = "libngspice.so.0"

SendChar = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
SendStat = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
ControlledExit = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_int, ctypes.c_bool, ctypes.c_bool, ctypes.c_int, ctypes.c_void_p
)
SendData = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
SendInitData = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p)
BGThreadRunning = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_bool, ctypes.c_int, ctypes.c_void_p)


class NgSpice:
    def __init__(self, quiet=False):
        self.lib = ctypes.CDLL(LIB)
        self.quiet = quiet
        self.log = []
        self.exit_status = None

        self._send_char = SendChar(self._on_char)
        self._send_stat = SendStat(self._on_stat)
        self._exit = ControlledExit(self._on_exit)
        self._bg = BGThreadRunning(self._on_bg)

        self.lib.ngSpice_Init.restype = ctypes.c_int
        self.lib.ngSpice_Command.argtypes = [ctypes.c_char_p]
        self.lib.ngSpice_Command.restype = ctypes.c_int

        rc = self.lib.ngSpice_Init(
            self._send_char, self._send_stat, self._exit,
            ctypes.cast(None, SendData), ctypes.cast(None, SendInitData),
            self._bg, None,
        )
        if rc != 0:
            raise RuntimeError(f"ngSpice_Init failed: {rc}")

    # --- callbacks ---
    def _on_char(self, msg, ident, user):
        text = msg.decode("utf-8", "replace")
        self.log.append(text)
        # stderr/stdout prefiksy od ngspice
        for pref in ("stdout ", "stderr "):
            if text.startswith(pref):
                text = text[len(pref):]
                break
        if not self.quiet:
            print(text, flush=True)
        return 0

    def _on_stat(self, msg, ident, user):
        return 0

    def _on_exit(self, status, immediate, quit_exit, ident, user):
        self.exit_status = status
        return 0

    def _on_bg(self, not_running, ident, user):
        return 0

    # --- api ---
    def cmd(self, c):
        return self.lib.ngSpice_Command(c.encode())

    def run_file(self, path):
        path = os.path.abspath(path)
        # cwd = katalog netlisty, zeby wrdata/.include dzialaly relatywnie
        os.chdir(os.path.dirname(path))
        self.cmd(f"source {path}")
        # .control w pliku wykonuje sie automatycznie po source


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    ng = NgSpice()
    for f in sys.argv[1:]:
        print(f"\n{'='*70}\n== {f}\n{'='*70}", flush=True)
        ng.run_file(f)
    ng.cmd("quit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
