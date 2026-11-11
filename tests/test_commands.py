import logging
import unittest

from utils.commands import ERROR_OUTPUT_LINES, CommandError, run_command


class RunCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        root = logging.getLogger()
        self._level = root.level
        root.setLevel(logging.INFO)

    def tearDown(self) -> None:
        logging.getLogger().setLevel(self._level)

    def test_capture_returns_stdout(self):
        result = run_command(["echo", "hello"], capture=True)
        self.assertEqual(result.stdout, "hello\n")

    def test_input_is_text(self):
        result = run_command(["cat"], input="abc", capture=True)
        self.assertEqual(result.stdout, "abc")

    def test_env(self):
        result = run_command(
            ["sh", "-c", "echo $FOO"], env={"FOO": "bar"}, capture=True
        )
        self.assertEqual(result.stdout, "bar\n")

    def test_output_is_hidden_unless_debug(self):
        result = run_command(["sh", "-c", "echo out; echo err >&2"])
        # Captured, stderr merged into stdout in order
        self.assertEqual(result.stdout, "out\nerr\n")

    def test_output_is_shown_with_debug(self):
        logging.getLogger().setLevel(logging.DEBUG)
        with self.assertLogs("utils.commands", logging.DEBUG) as logs:
            result = run_command(["true"])
        # Inherited streams: nothing captured
        self.assertIsNone(result.stdout)
        self.assertIn("Running command", logs.records[0].getMessage())

    def test_failure_includes_output(self):
        with self.assertRaises(CommandError) as ctx:
            run_command(["sh", "-c", "echo out; echo err >&2; exit 3"])
        error = ctx.exception
        self.assertEqual(error.returncode, 3)
        self.assertEqual(
            str(error),
            "Command 'sh -c 'echo out; echo err >&2; exit 3'' failed with exit "
            "status 3:\n  out\n  err",
        )

    def test_failure_with_capture_includes_stdout_and_stderr(self):
        with self.assertRaises(CommandError) as ctx:
            run_command(["sh", "-c", "echo out; echo err >&2; exit 1"], capture=True)
        self.assertIn("  out\n  err", str(ctx.exception))

    def test_failure_output_is_truncated(self):
        with self.assertRaises(CommandError) as ctx:
            run_command(["sh", "-c", "seq 1 100; exit 1"])
        lines = str(ctx.exception).splitlines()[1:]
        self.assertEqual(len(lines), ERROR_OUTPUT_LINES)
        self.assertEqual(lines[-1], "  100")

    def test_failure_without_output(self):
        with self.assertRaises(CommandError) as ctx:
            run_command(["false"])
        self.assertEqual(
            str(ctx.exception), "Command 'false' failed with exit status 1"
        )

    def test_command_error_is_a_called_process_error(self):
        import subprocess

        self.assertTrue(issubclass(CommandError, subprocess.CalledProcessError))

    def test_missing_executable(self):
        with self.assertRaises(FileNotFoundError):
            run_command(["definitely-not-a-command-xyz"])


if __name__ == "__main__":
    unittest.main()
