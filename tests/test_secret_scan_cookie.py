"""VP-5: секрет-сканер отличает БЕЗОБИДНЫЙ литерал метода онбординга «cookie»
от РЕАЛЬНОГО Cookie-заголовка/секрета (Master Spec §30.3/§30.4).

Это формальная негативная проверка вместо обфускации в приёмочном харнессе:
приёмка реально отправляет метод «cookie» и ждёт COOKIE_UNSUPPORTED, а сканер
при этом остаётся строгим к настоящим заголовкам/credentials.
"""

from atlas_core.redaction import scan_for_secrets
from atlas_test_base import AtlasTestCase


class TestCookieScanPrecision(AtlasTestCase):
    def test_harmless_method_literals_not_flagged(self):
        # То, что реально пишет приёмка/API: строковый литерал метода и стабильный код.
        for line in (
            'onb = client.post(url, json={"method": "cookie"})',
            'assert resp.json()["error"]["code"] == "COOKIE_UNSUPPORTED"',
            '{"method": "cookie"}',
            'return JSONResponse({"error": {"code": "COOKIE_UNSUPPORTED"}})',
        ):
            self.assertEqual(scan_for_secrets(line), [],
                             f"ложное срабатывание на безобидном литерале: {line!r}")

    def test_real_cookie_header_still_detected(self):
        # Настоящие заголовки/секреты обязаны срабатывать (сканер НЕ ослаблен).
        for line in (
            "Cookie: sessionKey=sk-ant-abcdef0123456789ghij",
            "set-cookie: session-token=AbCdEf0123456789wxyz",
            "cookie=__Secure-next-auth.session-token=abcdef0123456789xyz",
        ):
            rules = {h.rule for h in scan_for_secrets(line)}
            self.assertTrue(rules & {"cookie_header", "session_cookie", "anthropic_key"},
                            f"настоящий cookie-секрет НЕ пойман: {line!r}")


if __name__ == "__main__":
    import unittest
    unittest.main()
