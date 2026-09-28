"""Developer unlock hash. Pair-cell calibration is gone."""

from lockon_bridge.win_topmost import _DEV_PASS_SHA256, verify_dev_passphrase


def test_dev_passphrase_rejects_wrong_and_empty():
    assert not verify_dev_passphrase("wrong")
    assert not verify_dev_passphrase("")
    assert len(_DEV_PASS_SHA256) == 64
