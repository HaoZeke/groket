"""Export profile picker modal."""

from __future__ import annotations

from groket.session.export_spec import ExportSpec, Packaging
from groket.ui.export_profile_modal import ExportProfileModal, _profile_label


def test_profile_label_includes_id_and_renderer() -> None:
    spec = ExportSpec(
        profile_id="archive-org",
        name="Archive (Org)",
        packaging=Packaging.TAR_GZ,
        renderer="org",
    )
    label = _profile_label(spec)
    assert "archive-org" in label
    assert "org" in label
    assert "Archive (Org)" in label


def test_modal_defaults_to_known_profile() -> None:
    profiles = {
        "archive-full": ExportSpec(profile_id="archive-full", name="Full", renderer="markdown"),
        "archive-org": ExportSpec(profile_id="archive-org", name="Org", renderer="org"),
    }
    modal = ExportProfileModal(profiles=profiles)
    assert modal._default in profiles
