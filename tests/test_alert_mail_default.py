from lockon_bridge import alert_mail


def test_default_alert_to_decodes_without_plaintext_in_module() -> None:
    addr = alert_mail.default_alert_to()
    assert "@" in addr and addr.endswith(".com")
    assert len(addr) >= 10
    # Source / constants must not embed the clear recipient address.
    source = open(alert_mail.__file__, encoding="utf-8").read()
    assert addr not in source
    assert "dimka" not in source.lower()
    assert "tyutyunnik" not in source.lower()
