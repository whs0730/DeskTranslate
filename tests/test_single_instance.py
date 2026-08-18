from __future__ import annotations

import unittest
from uuid import uuid4

from desktranslate.single_instance import AlreadyRunningError, SingleInstance


def unique_name() -> str:
    return f"DeskTranslate.Tests.{uuid4()}"


class SingleInstanceTests(unittest.TestCase):
    def test_only_one_owner_can_acquire_and_release_is_reusable(self) -> None:
        name = unique_name()
        first = SingleInstance(name)
        second = SingleInstance(name)

        try:
            self.assertTrue(first.acquire())
            self.assertTrue(first.acquire())
            self.assertTrue(first.acquired)
            self.assertFalse(second.acquire())
            self.assertFalse(second.acquired)

            first.release()
            first.release()
            self.assertFalse(first.acquired)

            self.assertTrue(second.acquire())
            self.assertTrue(second.acquired)
        finally:
            first.release()
            second.release()

    def test_secondary_instance_can_signal_primary(self) -> None:
        name = unique_name()
        primary = SingleInstance(name)
        secondary = SingleInstance(name)

        try:
            self.assertTrue(primary.acquire())
            self.assertFalse(primary.poll_activation())
            self.assertFalse(secondary.acquire())

            self.assertTrue(secondary.signal_existing())
            self.assertTrue(primary.poll_activation())
            self.assertFalse(primary.poll_activation())
        finally:
            secondary.release()
            primary.release()

    def test_signal_existing_returns_false_without_primary(self) -> None:
        instance = SingleInstance(unique_name())
        self.assertFalse(instance.signal_existing())

    def test_context_manager_releases_lock_and_reports_contention(self) -> None:
        name = unique_name()
        contender = SingleInstance(name)

        with SingleInstance(name) as primary:
            self.assertTrue(primary.acquired)
            with self.assertRaises(AlreadyRunningError):
                with contender:
                    self.fail("contended context must not be entered")

        with contender:
            self.assertTrue(contender.acquired)


if __name__ == "__main__":
    unittest.main()
