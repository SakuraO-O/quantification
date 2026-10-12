import importlib.util
import tempfile
import threading
import unittest
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen


class DashboardPreviewTest(unittest.TestCase):
    def test_preview_matches_pages_bundle_and_uses_loopback(self):
        root = Path(__file__).resolve().parents[1]
        script = root / "scripts/serve_dashboard.py"
        spec = importlib.util.spec_from_file_location("serve_dashboard", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertEqual(module.LOOPBACK, "127.0.0.1")
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            module.build_preview(target)
            self.assertEqual(
                {path.name for path in target.iterdir()},
                {deployed for _, deployed in module.PAGE_ASSETS},
            )
            for source, deployed in module.PAGE_ASSETS:
                self.assertEqual(
                    (root / "codex" / source).read_bytes(),
                    (target / deployed).read_bytes(),
                )

            handler = partial(module.PreviewHandler, directory=str(target))
            try:
                server = ThreadingHTTPServer((module.LOOPBACK, 0), handler)
            except PermissionError:
                self.skipTest("当前执行环境禁止绑定本地回环端口")
            with server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f"http://{module.LOOPBACK}:{server.server_port}"
                    with urlopen(base + "/", timeout=3) as response:
                        self.assertEqual(response.read(), (target / "index.html").read_bytes())
                        self.assertEqual(response.headers["Cache-Control"], "no-store")
                    with urlopen(base + "/dashboard_runtime.js", timeout=3) as response:
                        self.assertEqual(response.read(), (target / "dashboard_runtime.js").read_bytes())
                finally:
                    server.shutdown()
                    thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
