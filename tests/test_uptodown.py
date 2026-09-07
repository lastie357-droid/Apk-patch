import unittest

from server import (
    find_uptodown_download_url,
    is_uptodown_url,
    parse_uptodown_results,
)


HTML = """
<html>
  <head>
    <meta property="og:title" content="Termux">
    <meta property="og:description" content="A terminal emulator for Android">
    <meta property="og:image" content="/assets/termux.png">
  </head>
  <body>
    <a href="https://termux.en.uptodown.com/android">
      <img src="/assets/termux.png">Termux
    </a>
    <a href="https://dw.uptodown.com/dwn/example/termux.apk">Download</a>
  </body>
</html>
"""


class UptodownAdapterTests(unittest.TestCase):
    def test_search_results_include_app_pages_not_download_hosts(self):
        results = parse_uptodown_results(HTML, "termux")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Termux")
        self.assertEqual(results[0]["url"], "https://termux.en.uptodown.com/android")

    def test_download_link_is_resolved_from_app_page(self):
        self.assertEqual(
            find_uptodown_download_url(
                "https://termux.en.uptodown.com/android",
                HTML,
            ),
            "https://dw.uptodown.com/dwn/example/termux.apk",
        )

    def test_store_url_validation_rejects_external_hosts(self):
        self.assertTrue(is_uptodown_url("https://termux.en.uptodown.com/android"))
        self.assertTrue(is_uptodown_url("https://dw.uptodown.com/dwn/example/app.apk"))
        self.assertFalse(is_uptodown_url("https://example.com/app.apk"))
        self.assertFalse(is_uptodown_url("file:///tmp/app.apk"))


if __name__ == "__main__":
    unittest.main()