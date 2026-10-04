import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "samples")
MINPRESET = os.path.join(SAMPLES, "firefly_minpreset.lua")
STATIC = os.path.join(SAMPLES, "static_protected.lua")
DYNAMIC = os.path.join(SAMPLES, "dynamic_protected.lua")
DYNAMIC_SOURCE = os.path.join(SAMPLES, "dynamic_source.lua")
HELLO = 'print("Hello from a deobfuscated script lol")\n'


def run(*args):
    return subprocess.run(
        [sys.executable, "-m", "firefly_deobf", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def lua_binary():
    for name in ("lua", "lua5.4", "lua5.3", "luajit", "texlua"):
        path = shutil.which(name)
        if path:
            return path
    return None


class MinpresetTest(unittest.TestCase):
    def test_payload(self):
        result = run(MINPRESET)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, HELLO)

    def test_report_status(self):
        self.assertIn("fully evaluated", run(MINPRESET, "--report").stderr)

    def test_leading_comment(self):
        with open(MINPRESET, "r", encoding="latin-1") as fh:
            src = fh.read()
        with tempfile.NamedTemporaryFile("w", suffix=".lua", encoding="latin-1", delete=False) as fh:
            fh.write("-- header comment\n" + src)
            path = fh.name
        try:
            result = run(path)
        finally:
            os.remove(path)
        self.assertEqual(result.stdout, HELLO)

    def test_deterministic(self):
        self.assertEqual(run(MINPRESET).stdout, run(MINPRESET).stdout)

    def test_invalid_input(self):
        with tempfile.NamedTemporaryFile("w", suffix=".lua", delete=False) as fh:
            fh.write('print("plain")')
            path = fh.name
        try:
            result = run(path)
        finally:
            os.remove(path)
        self.assertEqual(result.returncode, 1)
        self.assertIn("error:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class StaticSampleTest(unittest.TestCase):
    def test_fully_evaluated(self):
        result = run(STATIC, "--report")
        self.assertEqual(result.returncode, 0)
        self.assertIn("fully evaluated", result.stderr)

    def test_key_lines(self):
        out = run(STATIC).stdout
        for line in (
            'print("counter", 30)',
            'print("fib", "0,1,1,2,3,5,8,13,21,34,55")',
            'print("Vec(4,6)", 52, 8, 12)',
            'print(11, 21, 12, 13)',
            'print("FOX THE BROWN JUMPS QUICK")',
            'print("collatz", 111)',
            'print("grid", "122/312/331")',
            'print("003.1|ab  |ff")',
        ):
            self.assertIn(line, out)

    def test_strict_env_stops_on_unknown_global(self):
        result = run(STATIC, "--report", "--strict-env")
        self.assertIn("partial", result.stderr)


class DynamicSampleTest(unittest.TestCase):
    def test_partial_output(self):
        result = run(DYNAMIC, "--report")
        self.assertEqual(result.returncode, 0)
        self.assertIn("partial", result.stderr)
        self.assertIn("os.time()", result.stdout)
        self.assertIn("RESUME", result.stdout)

    @unittest.skipIf(lua_binary() is None, "no Lua interpreter available")
    def test_runtime_matches_source(self):
        lua = lua_binary()
        prelude = "os.time = function() return 1000003 end\n"
        with open(DYNAMIC_SOURCE, "r", encoding="latin-1") as fh:
            source = fh.read()
        deob = run(DYNAMIC).stdout
        outputs = []
        for body in (source, deob):
            with tempfile.NamedTemporaryFile("w", suffix=".lua", encoding="latin-1", delete=False) as fh:
                fh.write(prelude + body)
                path = fh.name
            try:
                proc = subprocess.run([lua, path], capture_output=True, text=True, timeout=120)
            finally:
                os.remove(path)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            outputs.append(proc.stdout)
        self.assertEqual(outputs[0], outputs[1])


class StructuredOutputTests(unittest.TestCase):
    def test_ast_all_static_sample(self):
        result = run(STATIC, "--ast-all")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        self.assertRegex(out, r"for r\d+ = 1, 3 do")
        self.assertIn("ipairs(", out)
        self.assertIn("pairs(", out)
        self.assertIn('print("counter"', out)
        self.assertIn('print("fib"', out)

    def test_ast_all_minpreset(self):
        result = run(MINPRESET, "--ast-all")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('print("Hello from a deobfuscated script lol")', result.stdout)

    def test_ast_partial_dynamic_sample(self):
        result = run(DYNAMIC, "--ast", "--report")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("structured remainder", result.stderr)
        self.assertIn('print("high")', result.stdout)
        self.assertIn('print("low")', result.stdout)
        self.assertNotIn("RESUME", result.stdout)

    def test_stress_files_present(self):
        stress = os.path.join(ROOT, "tests", "stress")
        self.assertTrue(os.path.isfile(os.path.join(stress, "stress_test.lua")))
        self.assertTrue(os.path.isdir(os.path.join(stress, "split")))


if __name__ == "__main__":
    unittest.main()
