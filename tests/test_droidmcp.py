"""Test suite for droidmcp.

Run with ``python -m pytest`` (or ``python -m unittest discover``).  Every test
here runs without a real device: adb is monkeypatched, which is exactly the
contract the toolkit promises — the parsing and protocol layers are pure.
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from droidmcp import adb, backends, device, input as input_mod, screen, ui
from droidmcp.server import MCPServer, ToolError

SAMPLE_DUMP = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.android.settings" content-desc="" clickable="false" enabled="true" bounds="[0,0][1080,2400]">
    <node index="1" text="设置" resource-id="com.android.settings:id/title" class="android.widget.TextView" package="com.android.settings" content-desc="" clickable="true" enabled="true" bounds="[100,200][400,260]" />
    <node index="2" text="" resource-id="com.android.settings:id/search" class="android.widget.Button" package="com.android.settings" content-desc="搜索" clickable="true" enabled="true" bounds="[900,200][1040,340]" />
    <node index="3" text="" resource-id="" class="android.widget.ListView" package="com.android.settings" content-desc="" clickable="false" enabled="true" scrollable="true" bounds="[0,400][1080,2400]" />
    <node index="4" text="零面积" resource-id="" class="android.widget.TextView" package="com.android.settings" content-desc="" clickable="false" enabled="true" bounds="[10,10][10,10]" />
  </node>
</hierarchy>
"""


class TestUiParsing(unittest.TestCase):
    def test_flattens_tree_and_skips_zero_area_nodes(self):
        nodes = ui.parse_dump(SAMPLE_DUMP)
        texts = [n.text for n in nodes]
        self.assertIn("设置", texts)
        self.assertNotIn("零面积", texts)
        self.assertEqual(len(nodes), 4)

    def test_center_is_computed_from_bounds(self):
        nodes = ui.parse_dump(SAMPLE_DUMP)
        search = next(n for n in nodes if n.content_desc == "搜索")
        self.assertEqual(search.center, (970, 270))

    def test_clickable_and_scrollable_flags(self):
        nodes = ui.parse_dump(SAMPLE_DUMP)
        listview = next(n for n in nodes if n.scrollable)
        self.assertFalse(listview.clickable)
        self.assertTrue(listview.scrollable)

    def test_parse_rejects_non_xml(self):
        with self.assertRaises(ValueError):
            ui.parse_dump("error: could not get idle state.")

    def test_find_matching_prefers_exact_text_then_fuzzy(self):
        nodes = ui.parse_dump(SAMPLE_DUMP)
        self.assertEqual(ui.find_matching(nodes, "设置")[0].text, "设置")
        # content-desc match works too
        self.assertEqual(ui.find_matching(nodes, "搜索")[0].content_desc, "搜索")

    def test_find_matching_exact_mode(self):
        nodes = ui.parse_dump(SAMPLE_DUMP)
        self.assertEqual(ui.find_matching(nodes, "设", exact=True), [])

    def test_summarize_drops_layout_noise(self):
        nodes = ui.parse_dump(SAMPLE_DUMP)
        summary = ui.summarize(nodes)
        labels = [item["text"] or item["desc"] for item in summary]
        self.assertIn("设置", labels)
        self.assertIn("搜索", labels)
        # The bare FrameLayout container and the scrollable ListView survive
        # only because they are actionable; nothing is unlabelled *and* inert.
        self.assertTrue(
            all(
                item["text"] or item["desc"] or item["id"] or item["clickable"] or item["scrollable"]
                for item in summary
            )
        )

    def test_summarize_collapses_unlabelled_containers(self):
        # A real MiuiHome dump is mostly inert FrameLayouts; they must not reach
        # the model. Build 40 such nodes plus one labelled button.
        inert = "".join(
            f'<node index="{i}" text="" resource-id="android:id/x{i}" class="android.widget.FrameLayout" '
            f'package="com.miui.home" content-desc="" clickable="false" enabled="true" '
            f'bounds="[0,{i * 10}][1080,{i * 10 + 10}]" />'
            for i in range(40)
        )
        xml = (
            "<?xml version='1.0' encoding='UTF-8'?><hierarchy rotation=\"0\">"
            + inert
            + '<node index="99" text="设置" resource-id="" class="android.widget.TextView" '
            'package="com.miui.home" content-desc="" clickable="true" enabled="true" '
            'bounds="[100,200][400,260]" /></hierarchy>'
        )
        nodes = ui.parse_dump(xml)
        self.assertEqual(len(nodes), 41)
        summary = ui.summarize(nodes)
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["text"], "设置")

    def test_summarize_relaxes_when_everything_is_unlabelled(self):
        # Some apps render into unlabelled views; reporting an empty screen
        # would leave the caller with nothing to act on.
        nodes = [
            ui.UiNode(
                index=i,
                text="",
                resource_id=f"com.app:id/view{i}",
                class_name="android.view.View",
                content_desc="",
                package="com.app",
                clickable=False,
                enabled=True,
                checkable=False,
                checked=False,
                scrollable=False,
                bounds=(0, i * 10, 100, i * 10 + 10),
            )
            for i in range(12)
        ]
        self.assertGreaterEqual(len(ui.summarize(nodes)), 8)


class TestInputEscaping(unittest.TestCase):
    def _roundtrip(self, text: str) -> str:
        """Escape, then undo the escaping the way the device shell does."""
        import shlex

        escaped = input_mod._escape_for_input(text)
        args = shlex.split(escaped)          # what the shell hands to `input`
        self.assertEqual(len(args), 1, f"escaping {text!r} produced {len(args)} tokens")
        return args[0]

    def test_spaces_become_percent_s(self):
        # adb's `input text` encodes a space as the literal token %s, and the
        # device decodes it back. Round-tripping must preserve the text.
        self.assertEqual(input_mod._escape_for_input("hello world"), "hello%sworld")
        self.assertEqual(self._roundtrip("hello world"), "hello%sworld")

    def test_user_text_containing_percent_s_is_preserved(self):
        # Regression: a naive implementation replaced the user's literal "%s"
        # with a space, silently corrupting their input.
        self.assertEqual(input_mod._escape_for_input("100%s"), "100%s")

    def test_shell_metacharacters_are_quoted(self):
        escaped = input_mod._escape_for_input("a; rm -rf /")
        self.assertTrue(escaped.startswith("'") and escaped.endswith("'"))
        self.assertEqual(self._roundtrip("a; rm -rf /"), "a;%srm%s-rf%s/")

    def test_single_quote_is_escaped(self):
        # A bare apostrophe would break out of shlex's single-quote wrapping.
        escaped = input_mod._escape_for_input("it's fine")
        self.assertTrue(escaped.startswith("'"))
        self.assertIn("'\"'\"'", escaped)  # the '"'"' escape sequence
        self.assertEqual(self._roundtrip("it's fine"), "it's%sfine")

    def test_unicode_and_backslash(self):
        self.assertEqual(self._roundtrip("中文 ok\\"), "中文%sok\\\\")

    def test_empty_text_is_noop(self):
        with mock.patch.object(adb, "shell") as shell:
            input_mod.type_text("")
        shell.assert_not_called()


class TestDeviceParsing(unittest.TestCase):
    def test_battery_parses_common_fields(self):
        payload = "Current Battery Service state:\n  AC powered: false\n  USB powered: true\n  status: 3\n  level: 87\n  temperature: 312\n"
        with mock.patch.object(adb, "shell", return_value=payload):
            info = device.battery()
        self.assertEqual(info["percent"], 87)
        self.assertEqual(info["status"], "discharging")
        self.assertAlmostEqual(info["temperature_c"], 31.2)

    def test_storage_parses_df_k(self):
        payload = "Filesystem     1K-blocks     Used Available Use% Mounted on\n/dev/block/dm-9 115343360 52428800  62914560  46% /data\n"
        with mock.patch.object(adb, "shell", return_value=payload):
            info = device.storage()
        self.assertEqual(info["total_bytes"], 115343360 * 1024)
        self.assertEqual(info["used_percent"], 45.5)

    def test_packages_sorted_and_stripped(self):
        payload = "package:com.b.app\npackage:com.a.app\n"
        with mock.patch.object(adb, "shell", return_value=payload):
            pkgs = device.packages()
        self.assertEqual(pkgs, ["com.a.app", "com.b.app"])

    def test_current_activity_regex(self):
        payload = "  mCurrentFocus=Window{1 u0 com.android.settings/com.android.settings.HomeActivity}"
        with mock.patch.object(adb, "shell", return_value=payload):
            self.assertEqual(
                device.current_activity(),
                "com.android.settings/com.android.settings.HomeActivity",
            )


class TestScreenSizes(unittest.TestCase):
    def test_png_size_reads_header(self):
        header = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (1080).to_bytes(4, "big") + (2400).to_bytes(4, "big")
        self.assertEqual(screen.png_size(header), (1080, 2400))

    def test_screen_size_prefers_override(self):
        payload = "Physical size: 1080x2400\nOverride size: 720x1600\n"
        with mock.patch.object(adb, "shell", return_value=payload):
            self.assertEqual(input_mod.screen_size(), (720, 1600))

    def test_screen_size_without_override_uses_physical(self):
        with mock.patch.object(adb, "shell", return_value="Physical size: 1080x2400\n"):
            self.assertEqual(input_mod.screen_size(), (1080, 2400))

    def test_screen_size_raises_on_garbage(self):
        with mock.patch.object(adb, "shell", return_value="error: closed\n"):
            with self.assertRaises(adb.AdbError):
                input_mod.screen_size()


class TestMcpProtocol(unittest.TestCase):
    def setUp(self):
        self.server = MCPServer(name="test", version="9.9.9")

        @self.server.tool("echo", "echo back", {"type": "object", "properties": {"v": {"type": "string"}}})
        def echo(v: str = ""):
            return {"content": [{"type": "text", "text": v}]}

        @self.server.tool("boom", "always fails")
        def boom():
            raise ToolError("nope")

    def test_initialize_reports_protocol_and_server_info(self):
        resp = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(resp["result"]["serverInfo"]["name"], "test")
        self.assertIn("protocolVersion", resp["result"])
        self.assertIn("tools", resp["result"]["capabilities"])

    def test_tools_list_shape(self):
        resp = self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in resp["result"]["tools"]}
        self.assertEqual(names, {"echo", "boom"})
        self.assertTrue(all("inputSchema" in t for t in resp["result"]["tools"]))

    def test_tools_call_success(self):
        resp = self.server.handle(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "echo", "arguments": {"v": "hi"}}}
        )
        self.assertEqual(resp["result"]["content"][0]["text"], "hi")

    def test_tools_call_error_is_reported_as_jsonrpc_error(self):
        resp = self.server.handle(
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "boom", "arguments": {}}}
        )
        self.assertEqual(resp["error"]["code"], -32602)
        self.assertEqual(resp["error"]["message"], "nope")

    def test_unknown_method(self):
        resp = self.server.handle({"jsonrpc": "2.0", "id": 5, "method": "wat"})
        self.assertEqual(resp["error"]["code"], -32601)

    def test_notifications_get_no_response(self):
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": "unknown/notification"}))

    def test_ping(self):
        resp = self.server.handle({"jsonrpc": "2.0", "id": 6, "method": "ping"})
        self.assertEqual(resp["result"], {})

    def test_stdio_loop_round_trip(self):
        import io

        stdin = io.StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
            + "\n\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            + "\n"
        )
        stdout = io.StringIO()
        self.server.run(stdin=stdin, stdout=stdout)
        lines = [json.loads(ln) for ln in stdout.getvalue().splitlines()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["id"], 1)
        self.assertEqual(lines[1]["id"], 2)

    def test_parse_error_produces_error_response(self):
        import io

        stdout = io.StringIO()
        self.server.run(stdin=io.StringIO("{not json}\n"), stdout=stdout)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["error"]["code"], -32700)


class TestAdbBackend(unittest.TestCase):
    def setUp(self):
        self.backend = backends.AdbBackend(binary="/usr/bin/adb")

    def test_list_devices_skips_header_and_blank_lines(self):
        payload = "List of devices attached\nR58M12345\tdevice\nemulator-5554\tdevice\n\n"
        with mock.patch.object(self.backend, "run") as run:
            run.return_value = adb.AdbResult(stdout=payload, stderr="", returncode=0)
            devices = self.backend.list_devices()
        self.assertEqual([d["serial"] for d in devices], ["R58M12345", "emulator-5554"])

    def test_argv_includes_serial_when_given(self):
        self.assertEqual(
            self.backend._argv(["shell", "id"], "R58M12345"),
            ["/usr/bin/adb", "-s", "R58M12345", "shell", "id"],
        )
        self.assertEqual(
            self.backend._argv(["shell", "id"], None),
            ["/usr/bin/adb", "shell", "id"],
        )

    def test_result_raise_for_status_carries_stderr(self):
        result = adb.AdbResult(stdout="", stderr="error: device offline", returncode=1)
        with self.assertRaises(adb.AdbError) as ctx:
            result.raise_for_status()
        self.assertIn("device offline", str(ctx.exception))

    def test_missing_adb_has_actionable_message(self):
        backend = backends.AdbBackend()
        with mock.patch.dict("os.environ", {"ADB_PATH": ""}, clear=False):
            with mock.patch("shutil.which", return_value=None):
                with self.assertRaises(adb.AdbError) as ctx:
                    _ = backend.binary
        message = str(ctx.exception)
        self.assertIn("ADB_PATH", message)
        self.assertIn("DROIDMCP_BACKEND=root", message)


class TestRootShellBackend(unittest.TestCase):
    """The root backend is what makes droidmcp usable without a USB cable."""

    def test_shell_wraps_in_escalator(self):
        backend = backends.RootShellBackend(elevator="su")
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(stdout=b"ok", stderr=b"", returncode=0)
            result = backend.shell("getprop ro.product.model")
        argv = run.call_args[0][0]
        self.assertEqual(argv, ["su", "-c", "getprop ro.product.model"])
        self.assertEqual(result.stdout, "ok")

    def test_rish_is_invoked_without_dash_c(self):
        backend = backends.RootShellBackend(elevator="rish")
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(stdout=b"", stderr=b"", returncode=0)
            backend.shell("input tap 1 2")
        self.assertEqual(run.call_args[0][0], ["rish", "input tap 1 2"])

    def test_single_synthetic_device_with_model(self):
        backend = backends.RootShellBackend(elevator="su")
        with mock.patch.object(backend, "shell") as shell:
            shell.return_value = adb.AdbResult(stdout="M2012K11AC\n", stderr="", returncode=0)
            devices = backend.list_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["model"], "M2012K11AC")
        self.assertEqual(devices[0]["state"], "device")

    def test_failed_command_raises_with_stderr(self):
        backend = backends.RootShellBackend(elevator="su")
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(stdout=b"", stderr=b"su: not found", returncode=127)
            # backends return the result; raising is the caller's decision
            result = backend.shell("id")
        self.assertFalse(result.ok)
        with self.assertRaises(adb.AdbError) as ctx:
            result.raise_for_status()
        self.assertIn("su: not found", str(ctx.exception))

    def test_current_activity_survives_grep_finding_nothing(self):
        # `grep` exits 1 on no match; that must not blow up device_info.
        with mock.patch.object(adb, "shell", return_value="") as shell:
            self.assertEqual(device.current_activity(), "")
        self.assertIn("|| true", shell.call_args[0][0])

    def test_backend_selection_from_env(self):
        backends.reset_backend()
        try:
            with mock.patch.dict("os.environ", {"DROIDMCP_BACKEND": "root"}, clear=False):
                self.assertIsInstance(backends.get_backend(), backends.RootShellBackend)
            backends.reset_backend()
            with mock.patch.dict("os.environ", {"DROIDMCP_BACKEND": "adb"}, clear=False):
                self.assertIsInstance(backends.get_backend(), backends.AdbBackend)
            backends.reset_backend()
            with mock.patch.dict("os.environ", {"DROIDMCP_BACKEND": "nonsense"}, clear=False):
                with self.assertRaises(adb.AdbError):
                    backends.get_backend()
        finally:
            backends.reset_backend()

    def test_custom_elevator_from_env(self):
        backends.reset_backend()
        try:
            with mock.patch.dict(
                "os.environ",
                {"DROIDMCP_BACKEND": "root", "DROIDMCP_ROOT_CMD": "rish"},
                clear=False,
            ):
                backend = backends.get_backend()
        finally:
            backends.reset_backend()
        self.assertEqual(backend.elevator, "rish")


if __name__ == "__main__":
    unittest.main()
