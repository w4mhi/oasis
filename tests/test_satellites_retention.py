"""
Pass-recording retention — the disk budget for configuration/sat-recordings/.

Nothing bounded this directory. rtl_fm writes 48 kHz/16-bit mono = 96 KB/s, so
MAX_SECONDS (20 min) is 115 MB per recording and a typical 10-minute LEO pass is
~58 MB, with no delete route and no sweep. A full root filesystem doesn't degrade
one feature on a field station — it takes the whole station down, which is the
same failure the ADS-B observations table already caused once.

Age alone can't bound this: the writer has no fixed rate, so auto-record (roadmap)
could lay down >1 GB/day and never trip a 72h rule. The budget is therefore
size-primary, with the age sweep off by default.
"""

import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "services", "satellites"))

import listen  # noqa: E402


def _wav(directory, name, size, mtime):
    path = os.path.join(directory, name)
    with open(path, "wb") as fh:
        fh.write(b"\0" * size)
    os.utime(path, (mtime, mtime))
    return path


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_under_budget_deletes_nothing(self):
        _wav(self.d, "a.wav", 100, 1_000)
        _wav(self.d, "b.wav", 100, 2_000)
        result = listen.prune_recordings(self.d, max_bytes=10_000)
        self.assertEqual(result["deleted"], [])
        self.assertEqual(result["bytes_freed"], 0)
        self.assertEqual(sorted(os.listdir(self.d)), ["a.wav", "b.wav"])

    def test_over_budget_deletes_oldest_first(self):
        _wav(self.d, "oldest.wav", 100, 1_000)
        _wav(self.d, "middle.wav", 100, 2_000)
        _wav(self.d, "newest.wav", 100, 3_000)
        result = listen.prune_recordings(self.d, max_bytes=250)
        self.assertEqual(result["deleted"], ["oldest.wav"])
        self.assertEqual(result["bytes_freed"], 100)
        self.assertEqual(sorted(os.listdir(self.d)), ["middle.wav", "newest.wav"])

    def test_deletes_only_as_many_as_needed(self):
        for i in range(5):
            _wav(self.d, f"r{i}.wav", 100, 1_000 + i)
        listen.prune_recordings(self.d, max_bytes=300)
        self.assertEqual(sorted(os.listdir(self.d)), ["r2.wav", "r3.wav", "r4.wav"])

    def test_newest_recording_survives_an_absurdly_small_budget(self):
        # Never leave the operator with nothing: the most recent capture is kept
        # even when it alone blows the budget, so a misconfigured cap can't eat
        # the pass that was just recorded.
        _wav(self.d, "old.wav", 100, 1_000)
        _wav(self.d, "just-recorded.wav", 5_000, 2_000)
        listen.prune_recordings(self.d, max_bytes=10)
        self.assertEqual(os.listdir(self.d), ["just-recorded.wav"])

    def test_ignores_non_wav_files(self):
        _wav(self.d, "keep.txt", 9_000, 1_000)
        _wav(self.d, "a.wav", 100, 2_000)
        listen.prune_recordings(self.d, max_bytes=150)
        self.assertIn("keep.txt", os.listdir(self.d))

    def test_excluded_path_is_never_deleted(self):
        # The in-flight capture's own file must survive its own sweep.
        _wav(self.d, "inflight.wav", 5_000, 1_000)   # oldest, would go first
        _wav(self.d, "b.wav", 100, 2_000)
        listen.prune_recordings(self.d, max_bytes=50,
                                exclude=os.path.join(self.d, "inflight.wav"))
        self.assertIn("inflight.wav", os.listdir(self.d))

    def test_missing_directory_is_not_an_error(self):
        result = listen.prune_recordings(os.path.join(self.d, "nope"), max_bytes=10)
        self.assertEqual(result["deleted"], [])
        self.assertEqual(result["total_bytes"], 0)

    def test_reports_the_total_it_left_behind(self):
        _wav(self.d, "a.wav", 100, 1_000)
        _wav(self.d, "b.wav", 100, 2_000)
        self.assertEqual(listen.prune_recordings(self.d, max_bytes=10_000)["total_bytes"], 200)


class AgeSweepTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_age_sweep_is_off_by_default(self):
        # Deleting under no space pressure is pure data loss — the one clean
        # Meteor pass shouldn't vanish while the card sits 95% empty.
        self.assertEqual(listen.MAX_AGE_SECONDS, 0)

    def test_age_sweep_when_enabled_drops_old_files_under_budget(self):
        now = 1_000_000
        _wav(self.d, "ancient.wav", 100, now - 400_000)   # ~4.6 days
        _wav(self.d, "fresh.wav", 100, now - 100)
        listen.prune_recordings(self.d, max_bytes=10_000,
                                max_age_seconds=259_200, now=now)   # 72h
        self.assertEqual(os.listdir(self.d), ["fresh.wav"])

    def test_age_sweep_respects_the_exclusion(self):
        now = 1_000_000
        _wav(self.d, "inflight.wav", 100, now - 400_000)
        listen.prune_recordings(self.d, max_bytes=10_000, max_age_seconds=259_200,
                                now=now, exclude=os.path.join(self.d, "inflight.wav"))
        self.assertEqual(os.listdir(self.d), ["inflight.wav"])


class FreeSpaceTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_enough_space_is_ok(self):
        ok, msg = listen.check_free_space(self.d, min_free_bytes=1,
                                          usage=lambda p: (100, 50, 50))
        self.assertTrue(ok)
        self.assertEqual(msg, "")

    def test_too_little_space_refuses_with_a_readable_reason(self):
        ok, msg = listen.check_free_space(
            self.d, min_free_bytes=1024 ** 3,
            usage=lambda p: (32 * 1024 ** 3, 31 * 1024 ** 3, 200 * 1024 ** 2))
        self.assertFalse(ok)
        self.assertIn("space", msg.lower())
        # The operator needs the numbers, not just "failed".
        self.assertIn("MB", msg + "GB")

    def test_unreadable_filesystem_does_not_block_recording(self):
        def boom(_):
            raise OSError("nope")

        ok, msg = listen.check_free_space(self.d, min_free_bytes=1024 ** 3, usage=boom)
        self.assertTrue(ok, "a stat failure must not become a hard block")


class DefaultsTest(unittest.TestCase):
    def test_documented_defaults(self):
        self.assertEqual(listen.MAX_TOTAL_BYTES, 2 * 1024 ** 3)    # 2 GB
        self.assertEqual(listen.MIN_FREE_BYTES, 1024 ** 3)         # 1 GB

    def test_budget_holds_a_useful_number_of_passes(self):
        # 10-minute pass at 48 kHz/16-bit mono.
        typical_pass = 10 * 60 * listen.SAMPLE_RATE * 2
        self.assertGreaterEqual(listen.MAX_TOTAL_BYTES // typical_pass, 30)

    def test_budget_leaves_room_on_the_smallest_supported_card(self):
        self.assertLess(listen.MAX_TOTAL_BYTES, 0.1 * 32 * 1024 ** 3)


if __name__ == "__main__":
    unittest.main()
