"""lcsc.py: normalising LCSC codes, finding them in footprint fields, and other tools' correction fields."""
import pytest

from jlcplace import lcsc


@pytest.mark.parametrize("value, code", [
    ("C2040", "C2040"), ("c2040", "C2040"), (" C2040 ", "C2040"), ("C2040\n", "C2040"), ("\tC2040\r\n", "C2040"),
    ("LCSC: C2040", "C2040"), ("lcsc:c2040", "C2040"), ("LCSC#C2040", "C2040"), ("JLCPCB Part # = C2040", "C2040"),
    ("JLC: C2040", "C2040"), ('"C2040"', "C2040"), ("C​2040", "C2040"), ("Ｃ２０４０", "C2040"),
    ("C2040 ", "C2040"), ("C1", "C1"), ("C123456789", "C123456789"),
    ("2040", ""), ("CXYZ", ""), ("C", ""), ("", ""), (None, ""), ("C 2040", ""), ("C2040, C1234", ""),
    ("C2040/C1234", ""), ("CC2040", ""), ("C-2040", ""), ("C2040A", ""), ("RP2040", ""), ("LCSC_R1", ""),
    ("C0402", ""), ("C0603", ""),                    # package sizes typed in the code field: LCSC has no leading zeros
    ("LCSC:", ""), ("LCSC: 2040", ""),
])
def test_normalize(value, code):
    assert lcsc.normalize(value) == code


def test_normalize_accepts_non_strings():
    assert lcsc.normalize(2040) == ""


def test_find_lcsc_order_and_case():
    assert lcsc.find_lcsc({"lcsc": "c2040"}) == ("C2040", "lcsc")
    assert lcsc.find_lcsc({"LCSC Part #": "C1", "LCSC": "C2"}) == ("C2", "LCSC")     # LCSC_FIELDS order, not dict order
    assert lcsc.find_lcsc({"Part Number": "C7", "JLCPCB Part #": "C8"}) == ("C8", "JLCPCB Part #")
    assert lcsc.find_lcsc({"lcsc_pn": "C5"}) == ("C5", "lcsc_pn")                     # "_" and " " are the same
    assert lcsc.find_lcsc({"LCSC Part#": "C6"}) == ("C6", "LCSC Part#")
    assert lcsc.find_lcsc({"LCSC": "C3", "lcsc": "C4"}) == ("C3", "LCSC")             # the exact spelling first
    assert lcsc.find_lcsc({"lcsc": "C4", "LCSC": "C3"}) == ("C3", "LCSC")


def test_find_lcsc_extra_names_come_first():
    fields = {"LCSC": "C2040", "Supplier Code": "C99"}
    assert lcsc.find_lcsc(fields) == ("C2040", "LCSC")
    assert lcsc.find_lcsc(fields, ["Supplier Code"]) == ("C99", "Supplier Code")
    assert lcsc.find_lcsc(fields, ("supplier code",)) == ("C99", "Supplier Code")
    assert lcsc.find_lcsc(fields, ["Missing"]) == ("C2040", "LCSC")


def test_invalid_values_are_skipped():
    # KiKit's test boards put placeholders in the LCSC field; the search moves on to the next name
    assert lcsc.find_lcsc({"LCSC": "LCSC_R1"}) == ("", "")
    assert lcsc.find_lcsc({"LCSC": "LCSC_R1", "JLCPCB": "C25804"}) == ("C25804", "JLCPCB")
    assert lcsc.find_lcsc({"LCSC": "", "LCSC Part": " c25804 "}) == ("C25804", "LCSC Part")
    assert lcsc.find_lcsc({}) == ("", "")


def test_mpn_counts_only_when_it_is_a_code():
    assert lcsc.find_lcsc({"MPN": "CL10A106KP8NNNC"}) == ("", "")
    assert lcsc.find_lcsc({"MPN": "RP2040 (C2040)"}) == ("", "")                     # contains a code, is not one
    assert lcsc.find_lcsc({"MPN": "C2040"}) == ("C2040", "MPN")
    assert lcsc.find_lcsc({"MPN": "C2040", "LCSC": "C19702"}) == ("C19702", "LCSC")
    # the dial-me-spinner fields: MPN is a part number, LCSC the code
    fields = {"Reference": "C10", "Value": "10u", "Datasheet": "", "Description": "", "MPN": "CL10A106KP8NNNC",
              "LCSC": "C19702", "Manufacturer": "Samsung Electro-Mechanics"}
    assert lcsc.find_lcsc(fields) == ("C19702", "LCSC")


def test_contract_names_are_searched_in_contract_order():
    contract = ["LCSC", "LCSC Part", "LCSC Part #", "LCSC#", "LCSC_PN", "LCSC PN", "JLCPCB Part", "JLCPCB Part #",
                "JLC", "JLC_PN", "JLCPCB", "Part #", "PN", "P/N", "Part No.", "Part Number", "MPN"]
    pos = [lcsc.LCSC_FIELDS.index(n) for n in contract]
    assert pos == sorted(pos) and lcsc.LCSC_FIELDS[-1] == "MPN"
    for i, name in enumerate(contract[:-1]):          # each name beats every later one
        fields = {n: f"C{100 + j}" for j, n in enumerate(contract[i:], i)}
        assert lcsc.find_lcsc(fields) == (f"C{100 + i}", name)


def test_correction_fields():
    fields = {"LCSC": "C2040", "FT Rotation Offset": " 90 ", "jlcpcb position offset": "0.1,0.2", "JLCRotOffset": "",
              "JLCPCB_CORRECTION": "0;0;180", "Rotation Offset": "-90", "Value": "10k", "JLCPCB Correction": None}
    assert lcsc.correction_fields(fields) == {"FT Rotation Offset": "90", "jlcpcb position offset": "0.1,0.2",
                                              "JLCPCB_CORRECTION": "0;0;180", "Rotation Offset": "-90"}
    assert lcsc.correction_fields({"LCSC": "C1"}) == {}
