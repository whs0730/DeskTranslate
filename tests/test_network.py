import io
import ssl
import unittest
from unittest.mock import MagicMock, Mock, patch
from urllib import error, request

from desktranslate.network import NetworkClient
from tests.test_translator import FakeResponse


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.api_request = request.Request("https://example.com/get?q=hello")

    @patch("desktranslate.network.request.build_opener")
    @patch("desktranslate.network.request.getproxies")
    def test_proxy_is_refreshed_between_requests(self, proxies, build):
        proxies.side_effect = [{"https": "http://127.0.0.1:7897"}, {}]
        build.return_value.open.return_value = FakeResponse(b"ok")
        client = NetworkClient()
        self.assertEqual(client.read(self.api_request, 10), b"ok")
        self.assertEqual(client.read(self.api_request, 10), b"ok")
        self.assertEqual(build.call_args_list[0].args[0].proxies, {"https": "http://127.0.0.1:7897"})
        self.assertEqual(build.call_args_list[1].args[0].proxies, {})

    @patch("desktranslate.network.request.build_opener")
    @patch("desktranslate.network.request.getproxies", return_value={"https": "http://127.0.0.1:7897"})
    def test_dead_proxy_falls_back_to_direct_with_a_clean_request(self, proxies, build):
        def fail(outgoing, *, timeout):
            outgoing.set_proxy("127.0.0.1:7897", "http")
            raise error.URLError(ConnectionRefusedError("proxy stopped"))

        first = Mock()
        first.open.side_effect = fail
        second = Mock()
        second.open.return_value = FakeResponse(b"translated")
        build.side_effect = [first, second]
        self.assertEqual(NetworkClient().read(self.api_request, 8), b"translated")
        self.assertEqual(build.call_args_list[1].args[0].proxies, {})
        self.assertEqual(second.open.call_args.args[0].host, "example.com")
        self.assertEqual(self.api_request.host, "example.com")
        self.assertEqual(first.open.call_args.kwargs["timeout"], 4)
        self.assertLessEqual(second.open.call_args.kwargs["timeout"], 8)

    @patch("desktranslate.network.request.build_opener")
    @patch("desktranslate.network.request.getproxies", side_effect=[{}, {"https": "http://127.0.0.1:7897"}])
    def test_vpn_enabled_during_failure_is_read_on_retry(self, proxies, build):
        first, second = Mock(), Mock()
        first.open.side_effect = TimeoutError()
        second.open.return_value = FakeResponse(b"ok")
        build.side_effect = [first, second]
        self.assertEqual(NetworkClient().read(self.api_request, 10), b"ok")
        self.assertEqual(build.call_args_list[1].args[0].proxies["https"], "http://127.0.0.1:7897")

    @patch("desktranslate.network.request.build_opener")
    @patch("desktranslate.network.request.getproxies", return_value={"https": "http://127.0.0.1:7897"})
    def test_system_mode_does_not_switch_to_direct(self, proxies, build):
        build.return_value.open.side_effect = error.URLError("connection failed")
        with self.assertRaises(error.URLError):
            NetworkClient("system").read(self.api_request, 5)
        build.assert_called_once()
        self.assertTrue(build.call_args.args[0].proxies)

    @patch("desktranslate.network.request.build_opener")
    @patch("desktranslate.network.request.getproxies")
    def test_direct_mode_ignores_proxy_configuration(self, proxies, build):
        build.return_value.open.return_value = FakeResponse(b"ok")
        self.assertEqual(NetworkClient("direct").read(self.api_request, 5), b"ok")
        proxies.assert_not_called()
        self.assertEqual(build.call_args.args[0].proxies, {})

    @patch("desktranslate.network.request.build_opener")
    def test_paid_request_is_not_automatically_replayed(self, build):
        build.return_value.open.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            NetworkClient().read(self.api_request, 30, allow_retry=False)
        build.assert_called_once()

    @patch("desktranslate.network.request.build_opener")
    def test_http_rejection_and_bad_certificate_are_never_retried(self, build):
        rejected = error.HTTPError(self.api_request.full_url, 429, "Too Many Requests", {}, io.BytesIO(b"quota"))
        for failure in (rejected, error.URLError(ssl.SSLCertVerificationError("invalid certificate"))):
            with self.subTest(failure=type(failure)):
                build.reset_mock()
                build.return_value.open.side_effect = failure
                with self.assertRaises(type(failure)):
                    NetworkClient().read(self.api_request, 10)
                build.assert_called_once()
        self.assertTrue(rejected.fp.closed)

    @patch("desktranslate.network.request.build_opener")
    @patch("desktranslate.network.time.monotonic", side_effect=[0, 0, 8])
    def test_elapsed_budget_prevents_another_attempt(self, monotonic, build):
        build.return_value.open.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            NetworkClient().read(self.api_request, 8)
        build.assert_called_once()

    @patch("desktranslate.network.request.build_opener")
    def test_connection_failure_during_read_is_also_retried(self, build):
        first = MagicMock()
        first.open.return_value.__enter__.return_value.read.side_effect = ConnectionResetError()
        second = Mock()
        second.open.return_value = FakeResponse(b"complete")
        build.side_effect = [first, second]
        self.assertEqual(NetworkClient().read(self.api_request, 5), b"complete")


if __name__ == "__main__":
    unittest.main()
