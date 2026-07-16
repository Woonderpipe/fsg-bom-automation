import os
import sys
from unittest.mock import MagicMock

# Ensure the src directory is in the path.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.browser import FSGBrowser


def test_fetch_site_options_stops_when_system_is_unavailable():
    browser = FSGBrowser(MagicMock())
    browser.page = MagicMock()
    browser.page.locator("#DTE_Field_assembly option").evaluate_all.return_value = ["Gearbox"]
    browser._force_system_change = MagicMock(return_value=False)

    assert browser.fetch_site_options("DT - Drivetrain") == []
    browser.page.locator("#DTE_Field_assembly option").evaluate_all.assert_not_called()


def test_create_part_keeps_custom_id_field_active():
    browser = FSGBrowser(MagicMock(request_timeout=30000))
    page = MagicMock()
    modal = MagicMock()
    modal.is_visible.return_value = False
    assembly_options = MagicMock()
    assembly_options.evaluate_all.return_value = [{"text": "Gearbox", "value": "12"}]
    custom_id = MagicMock()
    absent_field = MagicMock()
    absent_field.count.return_value = 0

    locators = {
        ".DTE_Action_Create": modal,
        "#DTE_Field_assembly option": assembly_options,
        "#DTE_Field_assembly": MagicMock(),
        "#DTE_Field_part": MagicMock(),
        "#DTE_Field_makebuy_0": MagicMock(),
        "#DTE_Field_makebuy_1": MagicMock(),
        "#DTE_Field_part_no_custom": custom_id,
        "#DTE_Field_sub_costs": absent_field,
        "#DTE_Field_sub_comments_emissions": absent_field,
    }
    page.locator.side_effect = lambda selector: locators[selector]
    browser.page = page
    browser._force_system_change = MagicMock(return_value=True)

    browser.create_part(
        {
            "system_label": "DT - Drivetrain",
            "assembly": "Gearbox",
            "part": "Sun Gear",
            "makebuy": "m",
            "comments": "",
            "quantity": "",
            "custom_id": "DT-001",
        }
    )

    custom_id.fill.assert_called_once_with("DT-001")
    custom_id.press.assert_not_called()
    page.keyboard.press.assert_not_called()


def test_normalize_decimal_handles_german_thousands_separator():
    assert FSGBrowser._normalize_decimal("1.234,56") == "1234.56"
