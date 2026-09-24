"""ops 模块离线单元测试（不连任何主机）。

运行: python -m unittest discover tests -v   （在项目根目录）
"""
import os
import tempfile
import unittest
from pathlib import Path

from ai_ops_tool.errors import ConfigError, GuardError
from ai_ops_tool.ops.client import HASH_CMD, MKDIR_CMD, _ALGO_HEXLEN
from ai_ops_tool.ops.cmds import _resolve_local, _transfer
from ai_ops_tool.ops.hosts import load_hosts


class ResolveLocalTest(unittest.TestCase):
    """本地路径自愈：MSYS 盘符风格 /e/x <-> E:/x。"""

    def test_realpath_untouched(self):
        # 真实存在的路径永远原样返回
        p = _resolve_local(str(Path(__file__).resolve()), must_exist=True)
        self.assertTrue(os.path.isfile(p))

    def test_msys_style_existing_file(self):
        cand = _resolve_local("/a/no-such-dir-xyz/no-such-file", must_exist=True)
        self.assertEqual(cand, "/a/no-such-dir-xyz/no-such-file")  # 不存在则原样

    def test_msys_style_get_target_converts(self):
        # get 目标（尚不存在）匹配盘符风格即转换
        cand = _resolve_local("/e/some/new-file.bin", must_exist=False)
        self.assertEqual(cand, "E:/some/new-file.bin")

    def test_non_msys_path_untouched(self):
        self.assertEqual(_resolve_local("/etc/hosts", must_exist=False),
                         "/etc/hosts")  # /etc 不是单字母盘符段


class HostsConfigTest(unittest.TestCase):
    """hosts.yaml 加载与校验。"""

    def _write(self, text: str) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False,
                                        encoding="utf-8")
        f.write(text)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_load_ok(self):
        path = self._write(
            "defaults:\n  retry: 3\nhosts:\n"
            "  h1:\n    type: linux\n    host: 127.0.0.1\n    user: root\n"
            "    password: pw\n    port: 2222\n"
        )
        hc = load_hosts(path)
        self.assertEqual(hc["defaults"]["retry"], 3)
        self.assertEqual(hc["hosts"]["h1"]["port"], 2222)
        self.assertEqual(hc["hosts"]["h1"]["type"], "linux")

    def test_missing_field(self):
        path = self._write("hosts:\n  h1:\n    type: linux\n    host: 127.0.0.1\n")
        with self.assertRaises(ConfigError):
            load_hosts(path)

    def test_bad_type(self):
        path = self._write(
            "hosts:\n  h1:\n    type: solaris\n    host: 127.0.0.1\n    user: root\n"
        )
        with self.assertRaises(ConfigError):
            load_hosts(path)

    def test_unregistered_host(self):
        path = self._write(
            "hosts:\n  h1:\n    type: linux\n    host: 127.0.0.1\n    user: root\n"
        )
        hc = load_hosts(path)
        from ai_ops_tool.ops.hosts import get_host
        with self.assertRaises(ConfigError):
            get_host(hc, "nope")


class MsyGuardTest(unittest.TestCase):
    """linux 主机 + 远端路径带盘符 = 污染拦截。"""

    def test_polluted_remote_path(self):
        path = self._write_tmp_hosts()
        hc = load_hosts(path)
        with self.assertRaises(GuardError):
            _transfer(hc, "h1", "whatever.bin", "E:/Users/tmp/x.bin", "md5", 5,
                      direction="put")

    def _write_tmp_hosts(self) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False,
                                        encoding="utf-8")
        f.write("hosts:\n"
                "  h1:\n    type: linux\n    host: 127.0.0.1\n    user: root\n"
                "    password: pw\n")
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name


class OsTemplatesTest(unittest.TestCase):
    """OS 适配点完整性：每种已知类型都有哈希与建目录模板。"""

    def test_all_types_covered(self):
        for t in ("linux", "windows"):
            self.assertIn(t, HASH_CMD)
            self.assertIn(t, MKDIR_CMD)
            for algo in ("md5", "sha256"):
                self.assertIn(algo, HASH_CMD[t])
        self.assertEqual(_ALGO_HEXLEN, {"md5": 32, "sha256": 64})


if __name__ == "__main__":
    unittest.main()
