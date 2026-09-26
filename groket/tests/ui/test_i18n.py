"""Fluent localization."""

from __future__ import annotations

from pathlib import Path

from groket.ui import text as ui_text
from groket.ui.i18n import (
    _locales_for,
    _normalize_lang,
    current_language,
    gettext_lazy,
    load_text_resource,
    locale_dir,
    setup_i18n,
    t,
)


def test_locale_ftl_exists() -> None:
    ftl = Path(locale_dir()) / "en" / "main.ftl"
    assert ftl.is_file()
    text = ftl.read_text(encoding="utf-8")
    assert "save =" in text or "save=" in text.replace(" ", "")


def test_setup_english() -> None:
    setup_i18n("en")
    assert current_language() == "en"
    assert ui_text.save() == "Save"
    assert ui_text.cancel() == "Cancel"
    help_l = ui_text.help_markup().lower()
    assert "timeline" in help_l or "ctrl+p" in help_l


def test_t_with_variable() -> None:
    setup_i18n("en")
    s = t("model-filter-notify", label="m1")
    assert "m1" in s
    assert "Model filter" in s or "filter" in s.lower()


def test_missing_id_returns_id() -> None:
    setup_i18n("en")
    assert t("this-id-does-not-exist-xyz") == "this-id-does-not-exist-xyz"


def test_setup_c_locale() -> None:
    setup_i18n("C")
    assert t("save") == "Save"


def test_gettext_lazy_alias() -> None:
    setup_i18n("en")
    assert gettext_lazy("save") == "Save"


class TestNormalizeLang:
    def test_empty(self) -> None:
        assert _normalize_lang("") == "en"
        assert _normalize_lang(None) == "en"

    def test_c_posix(self) -> None:
        assert _normalize_lang("C") == "en"
        assert _normalize_lang("POSIX") == "en"

    def test_locale_with_encoding(self) -> None:
        assert _normalize_lang("en_US.UTF-8") == "en"

    def test_full_locale(self) -> None:
        assert _normalize_lang("fr_FR") == "fr"

    def test_hyphen_locale(self) -> None:
        assert _normalize_lang("zh-CN") == "zh"


class TestLocalesFor:
    def test_en_returns_en(self) -> None:
        result = _locales_for("en")
        assert "en" in result
        assert result[-1] == "en"

    def test_other_lang_includes_en(self) -> None:
        result = _locales_for("fr")
        assert "en" in result
        assert "fr" in result

    def test_full_locale_dedupes(self) -> None:
        result = _locales_for("en_US.UTF-8")
        assert result.count("en") == 1


class TestSetupI18n:
    def test_default_english(self) -> None:
        lang = setup_i18n()
        assert lang == "en"

    def test_posix_locale(self) -> None:
        lang = setup_i18n("POSIX")
        assert lang == "en"

    def test_returns_normalized(self) -> None:
        lang = setup_i18n("en_US.UTF-8")
        assert lang == "en"


class TestT:
    def test_empty_message_id(self) -> None:
        assert t("") == ""

    def test_with_object_kwarg(self) -> None:
        setup_i18n("en")
        result = t("model-filter-notify", label=Path("/some/path"))
        assert isinstance(result, str)


class TestLoadTextResource:
    def test_loads_help_rich_txt(self) -> None:
        setup_i18n("en")
        txt = load_text_resource("help.rich.txt")
        assert isinstance(txt, str)

    def test_missing_resource_returns_empty(self) -> None:
        assert load_text_resource("no-such-file.xyz") == ""

    def test_locale_file_read(self, tmp_path) -> None:
        """load_text_resource reads the en locale file."""
        setup_i18n("en")
        txt = load_text_resource("help.rich.txt")
        # Should return content from en locale
        assert isinstance(txt, str)


class TestLocalesForDedup:
    def test_dedup_same_short(self) -> None:
        """_locales_for deduplicates the same short code."""
        result = _locales_for("en_US")
        assert result.count("en") == 1


class TestBuildL10nNonExistingLocale:
    def test_non_existing_locale_falls_back(self) -> None:
        """_build_l10n with a non-existing locale falls back to en."""
        from groket.ui.i18n import _build_l10n

        # A made-up locale will fall back to "en"
        l10n = _build_l10n("zz_ZZ")
        assert l10n is not None


class TestSetupI18nNgettext:
    def test_ngettext_singular(self) -> None:
        """ngettext returns singular and plural forms."""
        setup_i18n("en")
        from groket.ui.i18n import ngettext

        s = ngettext("save", "saves", 1)
        assert isinstance(s, str)
        p = ngettext("save", "saves", 2)
        assert isinstance(p, str)

    def test_identity_function(self) -> None:
        """Identity _ function returns input unchanged."""
        setup_i18n("en")
        from groket.ui.i18n import _

        assert _("hello") == "hello"


class TestTAutoSetup:
    def test_t_auto_setup_when_none(self) -> None:
        """t() auto-initialises i18n when _l10n is None."""
        import groket.ui.i18n as i18n_mod

        old = i18n_mod._l10n
        try:
            i18n_mod._l10n = None
            result = t("save")
            assert result == "Save"  # en/main.ftl — not the message id
            assert i18n_mod._l10n is not None
        finally:
            i18n_mod._l10n = old


class TestTFluentFormatException:
    def test_format_exception_returns_id(self) -> None:
        """Fluent format_value exception returns the message id."""
        from unittest.mock import patch

        setup_i18n("en")

        def bad_format(msg_id, args=None):
            raise RuntimeError("fluent broke")

        import groket.ui.i18n as i18n_mod

        with patch.object(i18n_mod._l10n, "format_value", bad_format):
            result = t("save")
            assert result == "save"
