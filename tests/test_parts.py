"""The rules fit and check share (parts.py): which two-terminal parts may be turned round, pin-name tokens, the KiCad
database key. Hand-built parts and EasyEDA footprints only."""
import pytest

from jlcplace import fit, geom
from jlcplace.check import nonpolar as check_nonpolar
from jlcplace.model import TOP, EEFootprint, EEPad, Pad, Part


def two(ref, footprint, names=("", ""), nets=("N1", "N2"), numbers=("1", "2"), half=1.475):
    pads = [Pad(numbers[0], -half, 0.0, 1.15, 1.8, net=nets[0], pin_name=names[0]),
            Pad(numbers[1], half, 0.0, 1.15, 1.8, net=nets[1], pin_name=names[1])]
    return Part(ref, TOP, 120.0, 80.0, 0.0, footprint=footprint, lcsc="C1", pads=pads)


def ee2(title="C1206", tags=(), names=None, tokens=()):
    ee = EEFootprint("C1", title, pads=[EEPad("1", -1.53, 0.0, 1.2, 1.8), EEPad("2", 1.53, 0.0, 1.2, 1.8)],
                     pin_names=dict(names or {"1": "1", "2": "2"}))
    if tags:
        ee.tags = list(tags)
    if tokens:
        ee.tokens = list(tokens)
    return ee


def both(part, ee=None):
    """parts.nonpolar, and check.nonpolar (which fit and check use) on the same part: they must agree."""
    from jlcplace.parts import nonpolar
    a, b = nonpolar(part, ee), check_nonpolar(part, ee)
    assert a == b, (part.ref, part.footprint, a, b)
    return a


def test_easyeda_part_categories_make_a_part_polar():
    """ee-1: a tantalum (JLC basic part C7171) on a generic C_1206 footprint. EasyEDA's title says nothing a regex
    could rely on, but its part categories say tantalum."""
    c5 = two("C5", "Capacitor_SMD:C_1206_3216Metric")
    assert both(c5) is True                                  # nothing on the KiCad side says polar
    assert both(c5, ee2("CAP-SMD_L3.2-W1.6-RD-C7171", tags=["Tantalum Capacitors"])) is False
    assert both(c5, ee2("CAP-SMD_BD6.3-L6.6-W6.6-FD", tags=["Aluminum Electrolytic Capacitors - SMD"])) is False
    assert both(c5, ee2("C1206", tags=["Multilayer Ceramic Capacitors MLCC - SMD/SMT"])) is True
    d = two("D7", "Diode_SMD:D_SMA")
    assert both(d, ee2("SMA", tags=["Schottky Barrier Diodes (SBD)"])) is False


def test_easyeda_polar_capacitor_titles_when_the_record_has_no_categories():
    """geo-2 / logic-3: EasyEDA titles its tantalum, polymer and electrolytic chip capacitors CAP-SMD_... or
    CASE-<letter>_... (its ceramics C0402, C1206, ...). Records without categories exist (C2909356), so the title
    counts when the categories are missing; categories that name a non-polar class win over it."""
    c = two("C8", "Capacitor_SMD:C_1206_3216Metric")
    for title in ("CAP-SMD_L3.2-W1.6-RD", "CAP-SMD_L3.2-W1.6", "CASE-B_3528", "CASE-A_3216",
                  "CAP-SMD_BD6.3-L6.6-W6.6-FD"):
        assert both(c, ee2(title)) is False, title
    assert both(c, ee2("C1206")) is True
    assert both(c, ee2("CAP-SMD_L3.2-W2.5", tags=["Multilayer Ceramic Capacitors MLCC - SMD/SMT"])) is True
    # an inductor with EasyEDA's direction token stays non-polar (L0402-RD, C77110)
    assert both(two("L1", "Inductor_SMD:L_0402_1005Metric"), ee2("L0402-RD", tokens=["RD"])) is True
    # 'tant' as a word in a custom footprint name ("tantal" missed C_Tant_3528)
    assert both(two("C9", "MyLib:C_Tant_3528")) is False


def test_x_is_a_connector_unless_it_is_a_crystal():
    """logic-2: IEC 81346 designates connectors X; a battery connector turned round is reversed."""
    x1 = two("X1", "Connector_JST:JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical", nets=("VBAT", "GND"))
    assert both(x1) is False
    assert both(x1, ee2("CONN-TH_2P-P2.00_S2B-PH-K-S")) is False
    assert both(two("X2", "Crystal:Crystal_SMD_2012-2Pin_2.0x1.2mm")) is True
    assert both(two("X3", "MyLib:XTAL_2012")) is True
    assert both(two("X4", "MyLib:Two_Pads"), ee2("OSC-SMD_L2.0-W1.2")) is True
    assert both(two("Y1", "MyLib:Two_Pads")) is True


def test_polar_footprint_words_are_words():
    """logic-9: 'led' in 'Angled' and 'elec' in 'KOHERelec' do not make a part polar; LED_, C_Elec_, CP_Elec_ do.
    A right-angle part is turned round only with its body facing the other way: never a harmless swap."""
    assert both(two("L1", "Inductor_SMD:L_KOHERelec_MDA7030")) is True
    assert both(two("L2", "Inductor_SMD:L_KOHERelec_MDA5030")) is True
    assert both(two("LED1", "LED_SMD:LED_0603_1608Metric")) is False
    assert both(two("C1", "Capacitor_SMD:C_Elec_5x5.8")) is False
    assert both(two("C2", "Capacitor_SMD:CP_Elec_5x5.8")) is False
    assert both(two("SW3", "Button_Switch_THT:SW_Tactile_SKHH_Angled")) is False


def test_bidirectional_parts_may_be_turned_round():
    """truth-7: a bidirectional TVS (pin functions A1 / A2, or EasyEDA's -BI title token) has no polarity."""
    assert both(two("D13", "Diode_SMD:D_SMF", names=("A1", "A2"))) is True
    assert both(two("D13", "Diode_SMD:D_SMF"), ee2("SMF_L2.8-W1.8-LS3.7-BI", tokens=["BI"],
                                                     names={"1": "C", "2": "A"})) is True
    assert both(two("D14", "Diode_SMD:D_SMF", names=("K", "A"))) is False
    assert both(two("D14", "Diode_SMD:D_SMF")) is False
    assert both(two("D15", "Diode_SMD:D_SMF", names=("A", "A2"))) is False      # A is the anode, not an index stem


def test_name_tokens_keep_letters_that_mean_something():
    """logic-8: a pin named K on a pad numbered K is the cathode; a BGA ball A1 named A1 says nothing."""
    assert fit.name_tokens("K", "K") == {"CATHODE"}
    assert fit.name_tokens("A", "A") == {"ANODE"}
    assert fit.name_tokens("+", "+") == {"ANODE"}
    assert fit.name_tokens("A1", "A1") == frozenset()
    assert fit.name_tokens("3", "3") == frozenset()
    assert fit.name_tokens("GND", "GND") == frozenset()          # repeats its number: no meaning of its own


def test_regulator_pin_names_are_synonyms():
    """ee-5: KiCad's AMS1117 symbol names VO / VI, EasyEDA's VOUT / VIN; others write IN / OUT."""
    for a, b in (("VO", "VOUT"), ("VI", "VIN"), ("IN", "VIN"), ("OUT", "VOUT")):
        assert fit.compare_names(fit.name_tokens(a), fit.name_tokens(b)) == 1, (a, b)
    assert fit.compare_names(fit.name_tokens("VO"), fit.name_tokens("VI")) == -1


def test_kicad_key_tells_parts_apart_by_their_pin_functions():
    """truth-1: one code on one footprint, pad 1 the cathode on D4 and the anode on D5: two database entries."""
    from jlcplace.parts import kicad_hash
    d4 = two("D4", "Diode_SMD:D_SOD-123", names=("K", "A"))
    d5 = two("D5", "Diode_SMD:D_SOD-123", names=("A", "K"))
    assert geom.pad_hash(d4.copper_pads()) == geom.pad_hash(d5.copper_pads())
    assert kicad_hash(d4) != kicad_hash(d5)
    assert kicad_hash(d4) == kicad_hash(two("D6", "Diode_SMD:D_SOD-123", names=("K", "A"), nets=("X", "Y")))
    # uninformative names leave the key as the pad hash: resistors do not split into many entries
    for names in (("", ""), ("~", "~"), ("1", "2")):
        r = two("R1", "Resistor_SMD:R_1206_3216Metric", names=names)
        assert kicad_hash(r) == geom.pad_hash(r.copper_pads()), names
    # the keys stored corrections are found by do not move when the comparison of names learns more: a pad named EP
    # keys as GND did, a name split at '/' adds no joined part, Input is no synonym there
    key = kicad_hash(two("U1", "Test:Test", names=("GND", "P0.00/XL1")))
    assert kicad_hash(two("U1", "Test:Test", names=("EP", "P0.00/XL1"))) == key == "sha1:" + __import__(
        "hashlib").sha1((geom.pad_hash(two("U1", "Test:Test").copper_pads()) + repr(
            [("1", ("GND",)), ("2", ("P0.00", "XL1"))])).encode()).hexdigest()[:16]


@pytest.mark.parametrize("number, w, kind, exempt", [("39", 0.6, "tht", "via"), ("39", 1.4, "tht", ""),
                                                     ("2", 1.2, "smd", "same pin"), ("7", 1.2, "smd", "")])
def test_uncovered_exemption(number, w, kind, exempt):
    """logic-6 / ee-4 / truth-6: KiCad copper that no EasyEDA pad lands on but belongs to a covered pin."""
    from jlcplace.parts import uncovered_exemption
    tab = Pad("2", 2.0, 0.0, 6.4, 5.8, net="VOUT")
    gnd = Pad("39", -4.0, 0.0, 0.9, 0.9, net="GND")
    assert uncovered_exemption(Pad("2", 1.0, 0.0, 0.6, 0.6, kind="tht", net="VOUT"), [tab]) == "inside"
    p = Pad(number, -6.0, 0.0, w, w if kind == "tht" else 1.2, kind=kind, net="GND" if number == "39" else "VOUT")
    assert uncovered_exemption(p, [tab, gnd]) == exempt
    # two equal pads of one number (MP, MP) are two pads: the missing one is never excused
    mp = [Pad("MP", -3.0, 2.0, 1.2, 1.8, net="GND"), Pad("MP", 3.0, 2.0, 1.2, 1.8, net="GND")]
    assert uncovered_exemption(mp[1], [mp[0]]) == ""
