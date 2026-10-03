"""Load errors render as one actionable table-cell line."""

from __future__ import annotations

from ytm_tui.app import _friendly_load_error

SESSION_HINT = "Session expired — press ctrl+l to re-paste your browser headers"


def test_http_401_maps_to_relogin_hint():
    message = "Server returned HTTP 401: Unauthorized.\nPlease sign in."
    assert _friendly_load_error(message) == SESSION_HINT


def test_http_403_maps_to_relogin_hint():
    assert _friendly_load_error("Server returned HTTP 403: Forbidden.") == SESSION_HINT


def test_missing_auth_maps_to_relogin_hint():
    message = "Please provide authentication before using this function"
    assert _friendly_load_error(message) == SESSION_HINT


def test_generic_errors_become_single_line():
    assert _friendly_load_error("line one\nline two    spaced") == "line one line two spaced"
    assert _friendly_load_error("Timeout: read timed out") == "Timeout: read timed out"
