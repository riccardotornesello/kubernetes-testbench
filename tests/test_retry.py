import unittest
from unittest import mock

from pydantic import ValidationError

from utils.retry import BackoffConfig, retry_with_backoff


class BackoffConfigTest(unittest.TestCase):
    def test_exponential_delay_capped(self):
        policy = BackoffConfig(initial_delay=1, multiplier=2, max_delay=5, jitter=0)
        self.assertEqual([policy.delay(n) for n in range(1, 6)], [1, 2, 4, 5, 5])

    def test_jitter_bounds(self):
        policy = BackoffConfig(initial_delay=10, max_delay=10, jitter=0.1)
        for _ in range(100):
            self.assertTrue(9 <= policy.delay(1) <= 11)

    def test_max_delay_lower_than_initial(self):
        with self.assertRaises(ValidationError):
            BackoffConfig(initial_delay=10, max_delay=5)

    def test_unknown_field(self):
        with self.assertRaises(ValidationError):
            BackoffConfig(retries=3)


@mock.patch("utils.retry.time.sleep")
class RetryWithBackoffTest(unittest.TestCase):
    policy = BackoffConfig(attempts=3, initial_delay=1, max_delay=10, jitter=0)

    def test_success_after_failures(self, sleep):
        calls = iter([ValueError("1"), ValueError("2"), "ok"])

        def func():
            result = next(calls)
            if isinstance(result, Exception):
                raise result
            return result

        with self.assertLogs("utils.retry", "WARNING"):
            self.assertEqual(retry_with_backoff(func, self.policy, ValueError), "ok")
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2])

    def test_last_error_reraised(self, sleep):
        func = mock.Mock(side_effect=ValueError("boom"))
        with self.assertLogs("utils.retry", "WARNING") as logs:
            with self.assertRaisesRegex(ValueError, "boom"):
                retry_with_backoff(func, self.policy, ValueError)
        self.assertEqual(func.call_count, 3)
        self.assertEqual(logs.records[-1].levelname, "ERROR")

    def test_other_errors_are_not_retried(self, sleep):
        func = mock.Mock(side_effect=KeyError("x"))
        with self.assertRaises(KeyError):
            retry_with_backoff(func, self.policy, ValueError)
        self.assertEqual(func.call_count, 1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
