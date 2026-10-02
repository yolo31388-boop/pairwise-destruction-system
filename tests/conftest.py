"""Pytest 配置：测试类使用了 unittest 风格断言但未继承 TestCase，
测试文件不可修改，因此在这里注入缺失的断言方法。"""

from tests.test_destruction import TestDestructionSystem


def _assert_is_not_none(self, obj, msg=None):
    assert obj is not None, msg or "unexpectedly None"


if not hasattr(TestDestructionSystem, "assertIsNotNone"):
    TestDestructionSystem.assertIsNotNone = _assert_is_not_none
