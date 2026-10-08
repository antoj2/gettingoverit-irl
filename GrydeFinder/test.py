"""
Python stand-in for the C# host (same idea, same protocol).  Starts script.py as a child process and
keeps the newest anchor sample available.

    python host_demo.py --script script.py --template GrydeFinder/pot_template.png --anchor-up 124 [--show]
"""
import argparse
import json
import subprocess
import sys
import threading
import time


class TrackerProcess:
    def __init__(self, script="script.py", python=sys.executable, template=None, anchor_up=None,
                 show=False, cwd=None, env=None):
        cmd = [python, "-u", script, "--ipc", "--show" if show else "--no-show"]
        if template:
            cmd += ["--template", template]
        if anchor_up is not None:
            cmd += ["--anchor-up", repr(float(anchor_up))]
        self._cmd, self._cwd, self._env = cmd, cwd, env
        self.latest = None            # newest "anchor" message (dict) or None
        self.ready = None             # the "ready" message
        self.messages = []            # every non-anchor message, in order
        self.bad_lines = []           # stdout lines that were not JSON (should stay empty)
        self.stderr_lines = []
        self.proc = None
        self.n_anchor = 0

    def start(self):
        self.proc = subprocess.Popen(self._cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, bufsize=1,
                                     cwd=self._cwd, env=self._env)
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        return self

    def _read_stdout(self):
        for line in self.proc.stdout:
            try:
                m = json.loads(line)
                kind = m["type"]
            except (ValueError, KeyError, TypeError):
                self.bad_lines.append(line.rstrip())
                continue
            if kind == "anchor":
                self.latest = m
                self.n_anchor += 1
            else:
                if kind == "ready":
                    self.ready = m
                self.messages.append(m)

    def _read_stderr(self):
        for line in self.proc.stderr:
            self.stderr_lines.append(line.rstrip())

    def send(self, command):
        try:
            self.proc.stdin.write(command + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def stop(self, grace=5.0):
        if self.proc.poll() is None:
            self.send("quit")
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(grace)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        return self.proc.returncode

    def wait_for(self, predicate, timeout):
        end = time.time() + timeout
        while time.time() < end:
            if predicate():
                return True
            if self.proc.poll() is not None:
                return predicate()
            time.sleep(0.02)
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default="script.py")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--template")
    ap.add_argument("--anchor-up", type=float)
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    t = TrackerProcess(a.script, a.python, a.template, a.anchor_up, a.show).start()
    print(f"[host] tracker pid {t.proc.pid}", flush=True)
    try:
        while t.proc.poll() is None:
            s = t.latest
            if s:
                x = "-" if s["x"] is None else f"{s['x']:7.1f}"
                y = "-" if s["y"] is None else f"{s['y']:7.1f}"
                print(f"\r{s['status']:<10} anchor=({x},{y}) angle={s['angle']} score={s['score']}   ",
                      end="", flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        print(f"\n[host] tracker exited with code {t.stop()}")


if __name__ == "__main__":
    main()