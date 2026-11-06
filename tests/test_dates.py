from datetime import date

from payradar.dates import period_from_terms


def test_period_from_terms_uses_labelled_line() -> None:
    text = "動物園行動支付上線\n活動時間：2019/07/01～2019/12/31\n注意事項：2026/1/1 起改版"
    assert period_from_terms(text) == (date(2019, 7, 1), date(2019, 12, 31))


def test_period_from_terms_ignores_unlabelled_or_yearless() -> None:
    assert period_from_terms("即日起至 11/30 止") == (None, None)
    assert period_from_terms("活動期間：7/1-9/30") == (None, None)
