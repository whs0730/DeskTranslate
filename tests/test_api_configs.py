import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from desktranslate.config import AppSettings, SettingsStore
from desktranslate.credentials import CredentialStore
from desktranslate.translator import LanguageDirection
from desktranslate.ui import DesktopApi, TranslationWindow


class FakeCredentials:
    """用字典模拟 Windows 凭据管理器，保留 target 参数语义。"""

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.store = dict(initial or {})
        self.saved: list[tuple[str, str | None]] = []
        self.deleted: list[str | None] = []

    def read(self, target: str | None = None) -> str | None:
        return self.store.get(target or CredentialStore.TARGET)

    def save(self, key: str, target: str | None = None) -> None:
        resolved = target or CredentialStore.TARGET
        self.store[resolved] = key
        self.saved.append((key, target))

    def delete(self, target: str | None = None) -> None:
        resolved = target or CredentialStore.TARGET
        self.store.pop(resolved, None)
        self.deleted.append(target)


def make_host(settings: AppSettings, credentials: FakeCredentials, store: SettingsStore) -> SimpleNamespace:
    host = SimpleNamespace(
        settings=settings,
        credentials=credentials,
        deepseek=Mock(),
        direction=LanguageDirection.AUTO,
        hotkey_error=None,
        native_glass=False,
    )
    host.save_settings = lambda: store.save(host.settings)
    return host


class ApiConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = SettingsStore(Path(self.directory.name) / "settings.json")

    def build(self, settings: AppSettings | None = None, secrets_map: dict[str, str] | None = None):
        credentials = FakeCredentials(secrets_map)
        host = make_host(settings or AppSettings(), credentials, self.store)
        return DesktopApi(host), host, credentials

    def test_creating_config_masks_key_and_never_returns_plaintext(self) -> None:
        api, host, credentials = self.build()
        response = api.save_api_config("", "工作账号", "deepseek-flash", "sk-abcdef1234567890")
        self.assertEqual(len(response["configs"]), 1)
        config = response["configs"][0]
        self.assertEqual(config["name"], "工作账号")
        self.assertTrue(config["has_key"])
        self.assertEqual(config["key_preview"], "sk-…7890")
        self.assertEqual(response["active_id"], config["id"])
        self.assertTrue(response["has_api_key"])
        self.assertEqual(
            credentials.store[f"DeskTranslate/DeepSeek/{config['id']}"],
            "sk-abcdef1234567890",
        )
        self.assertNotIn("sk-abcdef1234567890", str(api.get_settings()))

    def test_config_ids_generate_unique_credential_targets(self) -> None:
        api, _, _ = self.build()
        first = api.save_api_config("", "甲", "deepseek-flash", "sk-aaaaaaaaaaaa")["configs"][0]
        second = api.save_api_config("", "乙", "deepseek-v4-pro", "sk-bbbbbbbbbbbb")["configs"][1]
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(
            api.get_settings()["api_configs"][1]["key_preview"],
            "sk-…bbbb",
        )

    def test_renaming_and_switching_updates_active_configuration(self) -> None:
        api, host, _ = self.build()
        first = api.save_api_config("", "甲", "deepseek-flash", "sk-111111111111")["configs"][0]
        second = api.save_api_config("", "乙", "deepseek-v4-pro", "sk-222222222222")["configs"][1]
        renamed = api.save_api_config(second["id"], "乙-改", "deepseek-flash", "")
        self.assertEqual([item["name"] for item in renamed["configs"]], [first["name"], "乙-改"])
        activated = api.activate_api_config(first["id"])
        self.assertEqual(activated["active_id"], first["id"])
        self.assertTrue(activated["configs"][0]["active"])
        self.assertFalse(activated["configs"][1]["active"])
        self.assertEqual(host.settings.deepseek_model, "deepseek-flash")
        self.assertEqual(host.deepseek.model, "deepseek-flash")

    def test_deleting_last_configuration_is_rejected(self) -> None:
        api, _, credentials = self.build()
        config = api.save_api_config("", "唯一", "deepseek-flash", "sk-333333333333")["configs"][0]
        with self.assertRaisesRegex(ValueError, "至少保留一条配置"):
            api.delete_api_config(config["id"])
        self.assertEqual(len(api.get_settings()["api_configs"]), 1)
        self.assertIn(f"DeskTranslate/DeepSeek/{config['id']}", credentials.store)

    def test_deleting_active_configuration_falls_back_to_first_remaining(self) -> None:
        api, host, credentials = self.build()
        first = api.save_api_config("", "甲", "deepseek-flash", "sk-444444444444")["configs"][0]
        second = api.save_api_config("", "乙", "deepseek-v4-pro", "sk-555555555555")["configs"][1]
        api.activate_api_config(second["id"])
        remaining = api.delete_api_config(second["id"])
        self.assertEqual(remaining["active_id"], first["id"])
        self.assertEqual(remaining["has_api_key"], True)
        self.assertNotIn(f"DeskTranslate/DeepSeek/{second['id']}", credentials.store)
        self.assertEqual(host.settings.active_api_config, first["id"])

    def test_set_translation_settings_creates_first_configuration(self) -> None:
        api, host, credentials = self.build()
        result = api.set_translation_settings("deepseek", "deepseek-v4-pro", "sk-666666666666", False)
        self.assertTrue(result["has_api_key"])
        configs = api.get_settings()["api_configs"]
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]["name"], "默认配置")
        self.assertEqual(configs[0]["model"], "deepseek-v4-pro")
        self.assertIn("sk-666666666666", credentials.store.values())
        self.assertEqual(host.settings.translation_provider, "deepseek")

    def test_legacy_key_is_migrated_without_copying(self) -> None:
        window = SimpleNamespace(
            settings=AppSettings(),
            credentials=FakeCredentials({CredentialStore.TARGET: "sk-legacy"}),
            settings_store=self.store,
        )
        TranslationWindow._ensure_api_configs(window)
        configs = window.settings.api_configs
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].target, CredentialStore.TARGET)
        self.assertEqual(window.settings.active_api_config, configs[0].id)
        # 迁移只改内存，启动阶段不产生隐式写盘。
        self.assertFalse(self.store.path.exists())
        self.assertEqual(window.credentials.store[CredentialStore.TARGET], "sk-legacy")

    def test_missing_legacy_key_creates_no_configuration(self) -> None:
        window = SimpleNamespace(
            settings=AppSettings(),
            credentials=FakeCredentials(),
            settings_store=self.store,
        )
        TranslationWindow._ensure_api_configs(window)
        self.assertEqual(window.settings.api_configs, [])


if __name__ == "__main__":
    unittest.main()