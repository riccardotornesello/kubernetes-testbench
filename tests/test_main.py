import io
import unittest
from unittest import mock

import main


class ParseArgsTest(unittest.TestCase):
    def test_up(self):
        args = main.parse_args(["up", "c.yaml", "--force", "--out-dir", "o"])
        self.assertEqual(
            (args.command, args.config_file, args.force, args.out_dir),
            ("up", "c.yaml", True, "o"),
        )

    def test_up_without_force(self):
        self.assertFalse(main.parse_args(["up", "c.yaml"]).force)

    def test_config_only_is_up(self):
        args = main.parse_args(["c.yaml", "--log-format", "json"])
        self.assertEqual((args.command, args.log_format), ("up", "json"))

    def test_down(self):
        args = main.parse_args(["down", "c.yaml", "--log-level", "debug"])
        self.assertEqual((args.command, args.log_level), ("down", "DEBUG"))
        self.assertFalse(hasattr(args, "force"))


class ConfirmDeletionTest(unittest.TestCase):
    def confirm(self, existing, force=False, tty=True, answer=""):
        stdin = mock.Mock(isatty=mock.Mock(return_value=tty))
        stdin.readline.return_value = answer
        with (
            mock.patch("main.sys.stdin", stdin),
            mock.patch("main.sys.stderr", io.StringIO()) as stderr,
        ):
            main.confirm_deletion(existing, force)
        return stdin, stderr.getvalue()

    def test_nothing_to_delete(self):
        stdin, _ = self.confirm([], tty=False)
        stdin.readline.assert_not_called()

    def test_force(self):
        stdin, _ = self.confirm(["a"], force=True)
        stdin.readline.assert_not_called()

    def test_confirmed(self):
        for answer in ("y\n", "YES\n"):
            _, prompt = self.confirm(["a", "b"], answer=answer)
            self.assertIn("a, b", prompt)

    def test_refused(self):
        for answer in ("\n", "n\n", ""):
            with self.assertRaises(SystemExit):
                self.confirm(["a"], answer=answer)

    def test_no_terminal(self):
        with self.assertRaises(SystemExit):
            self.confirm(["a"], tty=False)


if __name__ == "__main__":
    unittest.main()
