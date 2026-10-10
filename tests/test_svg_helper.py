"""The shared helper for the SVG figure tests must find a real defect, or the tests that use it check nothing."""

from .svg import accessibility_problems, without_colours

GOOD = '<svg xmlns="http://www.w3.org/2000/svg" role="img"><title>A</title><desc>B</desc><style>.a{fill:#fff}</style><text>x</text></svg>'


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return path


def test_a_titled_and_described_image_has_no_problem(tmp_path):
    assert accessibility_problems([write(tmp_path, "good.svg", GOOD)]) == []


def test_a_missing_description_a_missing_role_and_a_blank_title_are_each_reported(tmp_path):
    no_desc = write(tmp_path, "no-desc.svg", GOOD.replace("<desc>B</desc>", ""))
    no_role = write(tmp_path, "no-role.svg", GOOD.replace(' role="img"', ""))
    blank_title = write(tmp_path, "blank.svg", GOOD.replace("<title>A</title>", "<title> </title>"))

    assert accessibility_problems([no_desc, no_role, blank_title]) == [
        "no-desc.svg has no <desc>",
        'no-role.svg has no role="img"',
        "blank.svg has no <title>",
    ]


def test_a_root_that_is_not_an_svg_element_is_reported(tmp_path):
    other = write(tmp_path, "other.svg", '<html role="img"><title>A</title><desc>B</desc></html>')

    assert accessibility_problems([other]) == ["other.svg is not an svg element", "other.svg has no <title>", "other.svg has no <desc>"]


def test_two_files_that_differ_only_in_the_style_element_have_the_same_text_without_colours(tmp_path):
    light = write(tmp_path, "light.svg", GOOD)
    dark = write(tmp_path, "dark.svg", GOOD.replace("#fff", "#000"))
    moved = write(tmp_path, "moved.svg", GOOD.replace("<text>x</text>", "<text>y</text>"))

    assert without_colours(light) == without_colours(dark)
    assert without_colours(light) != without_colours(moved)
