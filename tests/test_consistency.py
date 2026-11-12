# -*- coding: utf-8 -*-
"""一致性模块测试：环境锚点、姿势约束、批量切分。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import consistency  # noqa: E402
from consistency import (  # noqa: E402
    CONSISTENCY_NEGATIVE,
    FACE_LOCK,
    OTHER_POSE,
    POSE_NEGATIVE,
    SELFIE_POSE,
    match_scene,
    pose_clause,
    scene_anchor,
    split_prompts,
)


class SceneTests(unittest.TestCase):
    def test_same_place_same_scene(self):
        self.assertEqual(match_scene("公司"), match_scene("公司办公室"))

    def test_known_places_differ(self):
        self.assertNotEqual(match_scene("公司"), match_scene("家"))

    def test_unknown_place_is_stable(self):
        """认不出的地点也必须稳定，不能每次随机——随机就是背景乱跳的根源。"""
        first = match_scene("某个没听过的地方")
        second = match_scene("某个没听过的地方")
        self.assertEqual(first, second)
        self.assertTrue(first)

    def test_empty_place(self):
        self.assertEqual(match_scene(""), "")
        self.assertEqual(scene_anchor(""), "")

    def test_scene_anchor_contains_lock(self):
        anchor = scene_anchor("公司")
        self.assertIn("环境：", anchor)
        self.assertIn("背景", anchor)
        self.assertIn("不要更换地点", anchor)

    def test_office_scene_mentions_signage(self):
        """招牌是用户点名抱怨的点，办公场景必须把招牌写死。"""
        self.assertIn("招牌", match_scene("写字楼"))


class PoseTests(unittest.TestCase):
    def test_selfie_holds_phone_but_phone_not_in_frame(self):
        self.assertIn("举着手机", SELFIE_POSE)
        self.assertIn("手机本身不入镜", SELFIE_POSE)

    def test_selfie_forbids_crossed_arms(self):
        for word in ("双臂环抱", "抱臂", "抱胸"):
            self.assertIn(word, SELFIE_POSE)

    def test_other_pose_hides_phone(self):
        self.assertIn("不出现手机", OTHER_POSE)

    def test_pose_clause_switch(self):
        self.assertEqual(pose_clause(True), SELFIE_POSE)
        self.assertEqual(pose_clause(False), OTHER_POSE)


class NegativeTests(unittest.TestCase):
    def test_consistency_negative_covers_env_and_face(self):
        self.assertIn("different background", CONSISTENCY_NEGATIVE)
        self.assertIn("different face", CONSISTENCY_NEGATIVE)

    def test_pose_negative_covers_phone_and_arms(self):
        self.assertIn("手机", POSE_NEGATIVE)
        self.assertIn("抱臂", POSE_NEGATIVE)

    def test_face_lock_mentions_features(self):
        for word in ("脸型", "眼距", "眉形", "肤色"):
            self.assertIn(word, FACE_LOCK)


class BatchSplitTests(unittest.TestCase):
    def test_single_prompt(self):
        self.assertEqual(split_prompts("猫在窗边"), ["猫在窗边"])

    def test_newline_split(self):
        self.assertEqual(split_prompts("猫\n狗"), ["猫", "狗"])

    def test_semicolon_and_pipe(self):
        self.assertEqual(split_prompts("猫；狗｜兔子"), ["猫", "狗", "兔子"])

    def test_and_is_not_a_separator(self):
        """「猫和狗」是一张图，不能拆成两张。"""
        self.assertEqual(split_prompts("猫和狗"), ["猫和狗"])

    def test_limit(self):
        self.assertEqual(len(split_prompts("a\nb\nc\nd\ne", 3)), 3)

    def test_empty(self):
        self.assertEqual(split_prompts("   "), [])


if __name__ == "__main__":
    unittest.main()


class FaceFreezeTests(unittest.TestCase):
    """基准脸：第一次出图后冻结外观，之后改配置也不跟着漂。"""

    def setUp(self):
        import tempfile
        import test_main as T

        self.T = T
        self.root = tempfile.mkdtemp(prefix="amo-img-")
        self.plugin = T.make_plugin(character_appearance="圆脸，短发")
        self.plugin._plugin_data_dir = lambda: self.root

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def test_not_frozen_initially(self):
        self.assertEqual(self.plugin._face_anchor(), "")

    def test_freeze_once(self):
        self.plugin._freeze_face()
        self.assertEqual(self.plugin._face_anchor(), "圆脸，短发")

    def test_freeze_is_idempotent(self):
        self.plugin._freeze_face()
        self.plugin.config["character_appearance"] = "完全不同的脸"
        self.plugin._freeze_face()
        # 已经冻结过，不允许被后来的配置覆盖
        self.assertEqual(self.plugin._face_anchor(), "圆脸，短发")

    def test_face_clause_carries_anchor(self):
        self.plugin._freeze_face()
        clause = self.plugin._face_clause()
        self.assertIn("圆脸，短发", clause)
        self.assertIn("同一张脸", clause)


class FallbackLineTests(unittest.TestCase):
    def setUp(self):
        import test_main as T

        self.plugin = T.make_plugin()

    def test_error_line_is_human(self):
        line = self.plugin._fallback_line("error")
        self.assertNotIn("画不出来", line)
        self.assertNotIn("生成失败", line)
        self.assertTrue(line)

    def test_blocked_line_is_human(self):
        line = self.plugin._fallback_line("blocked")
        self.assertNotIn("画不了", line)
        self.assertTrue(line)

    def test_custom_config_wins(self):
        self.plugin.config["error_reply"] = "等我忙完"
        self.assertEqual(self.plugin._fallback_line("error"), "等我忙完")

    def test_legacy_default_is_replaced(self):
        """老配置里存着「画不出来」这类旧默认值时，也要换成人话。"""
        self.plugin.config["error_reply"] = "画不出来，等会儿再试试"
        self.assertNotIn("画不出来", self.plugin._fallback_line("error"))


class SceneMemoryTests(unittest.TestCase):
    """地点读不到时沿用上次环境，背景才不会乱跳。"""

    def setUp(self):
        import tempfile
        import test_main as T

        self.root = tempfile.mkdtemp(prefix="amo-scene-")
        self.plugin = T.make_plugin(character_appearance="圆脸")
        self.plugin._plugin_data_dir = lambda: self.root

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def test_remembers_known_place(self):
        self.plugin._remember_scene("公司")
        self.assertIn("写字楼", self.plugin._last_scene())

    def test_unknown_place_falls_back_to_last(self):
        self.plugin._remember_scene("公司")
        # 地点丢了（比如日程被清空），不能变成空环境
        self.assertIn("写字楼", self.plugin._remember_scene(""))

    def test_scene_anchor_accepts_fallback(self):
        anchor = scene_anchor("", "布置温馨的房间")
        self.assertIn("布置温馨的房间", anchor)
