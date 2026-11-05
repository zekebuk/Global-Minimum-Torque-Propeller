"""Assembly line plan for the RL2407 motor: stations, equipment, budget, capacity and unit cost.

Strategy (driven by the < USD 15k capital limit):

* **Buy** precision parts that need capital-heavy processes: laminations (bonded and
  epoxy-coated stacks from the lamination vendor), magnets (pre-magnetised -01/-02 sets), the
  CNC bell, base and ring, the shaft, and the bearings.
* **Make** everything that is labour plus fixtures: winding (hand winding with 3D-printed jigs;
  6 turns per tooth of single-strand AWG23 suits this), termination, impregnation, both
  subassemblies, balancing, final assembly, and 100 % end-of-line test.
* Automatic outrunner needle winders cost more than the whole budget (USD 15-25k+ listings).
  The upgrade path is the from-scratch 3-axis CNC winder in ``production.winder`` (budgeted, about
  3 min per stator), and later a commercial winder once volume justifies it.

Equipment prices come from the research of 2026-11-05 (see ``status``). Cycle times are
engineering estimates to be replaced by a time study during the pilot build. Currency
assumptions: 1 EUR = 1.16 USD, 1 GBP = 1.30 USD.
"""

from __future__ import annotations

from dataclasses import dataclass, field

EUR = 1.16


def _winder_bom_total() -> float:
    from bldc_motor.production.winder import bom_total

    return round(bom_total(), 2)


BUDGET_USD = 15_000.0
CONTINGENCY = 0.15


@dataclass(frozen=True)
class Equipment:
    key: str
    item: str
    station: str
    price_usd: float
    vendor: str
    url: str
    status: str  # page / snippet / listing / estimate
    notes: str = ""
    qty: int = 1

    @property
    def total(self) -> float:
        return self.price_usd * self.qty


EQUIPMENT: tuple[Equipment, ...] = (
    Equipment(
        "wind_jig",
        "Hand winding kit: 3D-printed tooth-indexing jig, wire tensioner, spool stand, counters (2 seats)",
        "S3",
        300.0,
        "DIY (P1S-printed)",
        "",
        "estimate",
        "No commercial outrunner hand jig found",
        1,
    ),
    Equipment(
        "cnc_winder",
        "From-scratch 3-axis CNC needle winder (docs/manufacturing/cnc_winder) - phase 2",
        "S3",
        _winder_bom_total(),
        "in-house build (BOM: cnc_winder/winder_bom.csv)",
        "",
        "estimate",
        "Toolpath verified in software; ~3 min/stator vs 10 min by hand",
    ),
    Equipment(
        "press",
        "Grizzly T26414 2-ton arbor press + turned/printed anvils",
        "S7/S8",
        271.47,
        "Grizzly",
        "https://www.grizzly.com/products/grizzly-2-ton-arbor-press/t26414",
        "snippet",
    ),
    Equipment(
        "oven",
        "Labnique 30 L forced-air oven, 300 C",
        "S6",
        998.0,
        "Labnique via reach.dog",
        "https://m.reach.dog/shop/labnique/products/digital-bench-top-forced-air-drying-oven-sterilizing-oven-with-over-heat-protection-and-adjustable-fan-speed-30l-1-1-cu-ft-300-c-800w-110v",
        "snippet",
        "Vent to outside for varnish solvents",
    ),
    Equipment("vent", "Inline fan + duct for oven/varnish corner", "S6", 150.0, "-", "", "estimate"),
    Equipment(
        "hipot",
        "GW Instek GPT-9801 AC 5 kV hipot",
        "S5/S10",
        1244.0,
        "TestEquity",
        "https://www.testequity.com/product/15249-9-GPT-9801",
        "snippet",
    ),
    Equipment(
        "milliohm",
        "Tonghui TH2512B+ 4-wire milliohm meter (alt. Hioki RM3548 $1,440)",
        "S5/S10",
        300.0,
        "LCSC (reference price, out of stock)",
        "https://lcsc.com/product-detail/Ohmmeter_Tonghui-TH2512B_C34373870.html",
        "snippet",
    ),
    Equipment(
        "psu",
        "Korad KWR103 60 V / 15 A programmable supply (no-load EOL test)",
        "S10",
        243.80 * EUR,
        "eleshop.eu",
        "https://eleshop.eu/korad-kwr103-power-supply.html",
        "snippet",
    ),
    Equipment(
        "stand",
        "Tyto Robotics Series 1585 thrust stand bundle (sample loaded test)",
        "S10",
        1075.0,
        "Tyto Robotics",
        "https://www.tytorobotics.com/products/series-1580-test-stand-bundle",
        "page",
        "Listed SOLD OUT on 2026-11-05: lead-time risk",
    ),
    Equipment(
        "tach",
        "UNI-T UT372 optical tachometer",
        "S10",
        72.99,
        "eBay",
        "https://www.ebay.com/itm/385065354507",
        "snippet",
    ),
    Equipment(
        "test_power",
        "6S packs (2), charger, ESCs, props, safety cage for loaded test",
        "S10",
        450.0,
        "-",
        "",
        "estimate",
    ),
    Equipment(
        "solder",
        "Hakko FX-888DX station + Pinecil V2 + Hakko FA-400 fume extractor",
        "S4",
        129.95 + 37.95 + 81.67,
        "SparkFun / Ameridroid / Hakko",
        "https://www.sparkfun.com/hakko-fx-888dx-soldering-station.html",
        "snippet",
    ),
    Equipment("solder_pot", "Small solder pot for enamel stripping of AWG23 ends", "S4", 60.0, "-", "", "estimate"),
    Equipment(
        "esd",
        "Desco 16310 ESD mat kit",
        "S4/S10",
        47.62,
        "Test Equipment Depot",
        "https://mcstaging.testequipmentdepot.com/desco-16310-trustat-b80-vinyl-mat-kit-blue-0080-in-x-24-in-x-36-in.html",
        "snippet",
        "",
        2,
    ),
    Equipment(
        "dispenser",
        "983A pneumatic dispenser + small compressor",
        "S7/S8",
        250.0,
        "eBay listings + compressor (est.)",
        "https://www.ebay.de/itm/406152295250",
        "listing",
    ),
    Equipment(
        "printer",
        "Bambu Lab P1S (fixtures, jigs, nests)",
        "all",
        399.0,
        "bambuhub tracker",
        "https://bambuhub.net/bambu-price-tracking",
        "snippet",
        "Being phased out; P2S $549",
    ),
    Equipment(
        "metrology",
        "Mitutoyo caliper + micrometer kit 64PKA076B",
        "S1",
        516.85,
        "Walmart",
        "https://walmart.com/c/brand/mitutoyo",
        "snippet",
    ),
    Equipment(
        "indicator",
        "Mitutoyo 64PKA079B dial indicator + magnetic base (runout)",
        "S1/S9",
        302.99,
        "riswll",
        "https://www.riswll.com/products/mitutoyo-64pka079b-mitutoyo-dial-indicator-0-to-1-in-0-100",
        "snippet",
    ),
    Equipment(
        "scale",
        "American Weigh AMW-SC-501 500 g x 0.01 g (balance weights, mass audit)",
        "S1/S9",
        25.99,
        "Walmart",
        "https://www.walmart.com/ip/28643541",
        "snippet",
        "Upgrade: Ohaus Scout STX422 $436.68",
    ),
    Equipment(
        "dmm",
        "Brymen BM235 multimeter",
        "S5",
        117.06,
        "TME",
        "https://www.tme.com/",
        "snippet",
        "Alt. Fluke 117 $227.57 (Provantage)",
    ),
    Equipment(
        "lcr",
        "DER EE DE-5000 LCR meter (phase inductance check)",
        "S10",
        181.25,
        "eBay",
        "https://www.ebay.com/p/13011030190",
        "snippet",
    ),
    Equipment(
        "gauss",
        "Gaussmeter / pole checker for magnet incoming + pole order",
        "S1/S8",
        150.0,
        "-",
        "",
        "estimate",
        "Not researched",
    ),
    Equipment(
        "balancer",
        "Vibromera Balanset-1A 2-plane balancer + in-house spindle fixture",
        "S9",
        1751.0 * EUR + 200.0,
        "Vibromera",
        "https://vibromera.eu/",
        "snippet",
        "Fallback: UAV belt balancer listing $3.8-5.2k",
    ),
    Equipment(
        "benches",
        "BenchPro RD3072 ESD workbench 30x72 in",
        "all",
        409.99,
        "Webstaurant",
        "https://www.webstaurantstore.com/benchpro-roosevelt-series-30-x-72-lisstat-esd-laminate-top-adjustable-workbench-with-gray-frame-and-round-front-edge-rd3072/595RD3072.html",
        "snippet",
        "Third bench later from contingency",
        2,
    ),
    Equipment(
        "shelving",
        "Uline H-8407 heavy-duty shelving",
        "S1/S11",
        200.0,
        "Uline",
        "https://www.uline.com/Product/Detail/H-8407/Industrial-Steel-Shelving/Heavy-Duty-Boltless-Shelving-48-x-24-x-72",
        "snippet",
        "",
        2,
    ),
    Equipment(
        "safety",
        "Safety: Z87 glasses, gloves, non-magnetic tweezers/tools, first aid, magnet spacers",
        "all",
        250.0,
        "-",
        "",
        "estimate",
    ),
    Equipment(
        "consumables",
        "Start-up consumables: Loctite 638/641/648/AA326+7649/243, varnish, 10 lb AWG23, silicone wire, sleeving, solder, IPA",
        "all",
        650.0,
        "see suppliers",
        "",
        "estimate",
    ),
)


@dataclass(frozen=True)
class Station:
    key: str
    name: str
    labour_min: float  # operator minutes per motor
    equipment: tuple[str, ...]
    fixtures: tuple[str, ...]
    checks: tuple[str, ...]
    batch_wait_h: float = 0.0  # cure / soak time (WIP, not labour)
    notes: str = ""


STATIONS: tuple[Station, ...] = (
    Station(
        "S1",
        "Goods-in inspection (sampling)",
        1.0,
        ("metrology", "indicator", "scale", "gauss"),
        ("Go/no-go bore pins Ø12.24, Ø9 H6; magnet thickness nest",),
        (
            "AQL 1.0 sampling: stator bore/OD/stack, bell/base critical bores, shaft Ø4/Ø5, magnet thickness + pole marking, bearing spin",
        ),
    ),
    Station("S2", "Stator prep", 0.5, (), ("Bore mandrel",), ("Bore free of coating; edge coating visual",)),
    Station(
        "S3",
        "Winding (hand, jig-indexed)",
        10.0,
        ("wind_jig", "cnc_winder"),
        ("Tooth-indexing jig with turn counter", "Wire tensioner"),
        ("6 turns/tooth, direction per DWG-003, no crossovers in slot, no wire above shoe",),
        notes="Bottleneck. ~72 turns + 12 transitions; CNC winder targets ~3 min",
    ),
    Station(
        "S4",
        "Termination (star point + leads)",
        5.0,
        ("solder", "solder_pot", "esd"),
        ("Lead-dressing comb", "Star-point sleeve holder"),
        ("Joint pull test 10 N (sample)", "Sleeving complete"),
    ),
    Station(
        "S5",
        "Electrical test 1",
        1.5,
        ("milliohm", "hipot", "dmm"),
        ("Kelvin clip nest",),
        ("R line-line 3 pairs (temp-corrected) within ±6 %, imbalance <= 2 %", "Hipot 1.05 kV AC 1 s <= 1 mA"),
    ),
    Station(
        "S6",
        "Impregnation (dip + cure)",
        1.0,
        ("oven", "vent"),
        ("Dip rack (12 stators)",),
        ("Cure log (time/temp)", "No varnish in bore"),
        batch_wait_h=3.0,
    ),
    Station(
        "S7",
        "Base subassembly",
        3.0,
        ("press", "dispenser"),
        ("Bearing press anvil (outer race)", "Stator-on-tube alignment jig (window W1)"),
        ("Bearings seated (feeler 0.02)", "Stator height + lead exit in W1"),
        batch_wait_h=0.5,
    ),
    Station(
        "S8",
        "Rotor subassembly",
        8.5,
        ("press", "dispenser", "gauss"),
        ("14-pole magnet indexing fixture", "Ring-into-bell press anvil", "Shaft press anvil + grub torque driver"),
        ("Pole order N/S alternating (gauss check)", "Magnet axial position", "Shaft runout <= 0.02 at prop thread"),
        batch_wait_h=0.5,
    ),
    Station(
        "S9",
        "Rotor balancing",
        4.0,
        ("balancer", "scale", "indicator"),
        ("Balancing spindle with Ø5 collet",),
        ("Residual unbalance <= 11 mg·mm (G2.5 @ 36k rpm)",),
    ),
    Station(
        "S10",
        "Final assembly + end-of-line test",
        5.3,
        ("press", "milliohm", "hipot", "psu", "tach", "lcr", "stand", "test_power", "esd"),
        ("E-clip tool", "EOL test nest with tach window"),
        (
            "Axial play 0.02-0.08 mm",
            "R + hipot repeat",
            "No-load Kv at 12 V within ±3σ (≈ ±5.5 %) of 1687 rpm/V per spec_sheet.md; I0 within limit",
            "Vibration/noise vs golden sample",
            "1 in 50: loaded sweep on Tyto stand vs predicted_stand_curve.csv",
        ),
    ),
    Station("S11", "Label + pack", 1.0, (), ("Serial label template",), ("Serial, Kv, R recorded in traveller",)),
)


UPGRADES = (
    (
        "Automatic outrunner needle winder (4-station)",
        "USD 15-16k listing (QIWEI) / 18k (Rimy) / 90-150k RMB (Vacuz article)",
        "when hand + CNC winding exceeds ~2 operators (> ~6k motors/yr)",
    ),
    ("Purpose-built UAV rotor balancer", "USD 3.8-5.2k listing", "if Balanset + spindle repeatability is insufficient"),
    ("Hioki RM3548 milliohm meter", "USD 1,440 (Transcat)", "temperature-compensated R for EOL traceability"),
    ("Ohaus Scout STX422 scale", "USD 436.68", "calibrated weighing"),
    ("Third ESD bench", "USD 409.99", "third operator"),
)


@dataclass
class LinePlan:
    equipment_total: float
    contingency: float
    capital_total: float
    within_budget: bool
    labour_min_per_motor: float
    motors_per_operator_day: float
    bottleneck: str
    capacity_per_year_single_shift: dict = field(default_factory=dict)
    unit_cost: dict = field(default_factory=dict)


PRODUCTIVE_MIN_PER_DAY = 7.0 * 60.0
WORK_DAYS = 230
LABOUR_RATE_USD_H = 25.0  # fully loaded assumption; set to your location


# Per-motor bill of materials. Each value is (pilot qty 50, volume 5k/yr, basis)
BOM = {
    "stator stack, bonded + coated (75 lams)": (15.00, 1.50, "estimate - QR (Motorneo / YouYou / Precision Micro)"),
    "magnets 14 pcs (-01/-02 set)": (
        21.00,
        1.12,
        "pilot est. K&J custom (QR); volume = Bestway listing upper $0.08/pc",
    ),
    "bell 7075 CNC + anodize": (25.00, 4.00, "estimate - QR (Xometry/PCBWay/JLCCNC); benchmark $35-75 unverified"),
    "base 7075 CNC + anodize": (20.00, 3.50, "estimate - QR"),
    "back-iron ring": (6.00, 0.80, "estimate - QR"),
    "shaft (turn + harden + grind)": (8.00, 1.20, "estimate - QR (Misumi custom)"),
    "bearings 2 x 684ZZ": (2 * 1.92 * EUR, 2 * 0.80, "pilot EZO EUR 1.92 (snippet); volume est. NMB/EZO distributor"),
    "magnet wire AWG23 (~5 g)": (0.30, 0.17, "$53-60/lb small spools; $15.5/lb 10 lb spool (BAE)"),
    "leads 3 x 150 mm 20 AWG silicone": (0.15, 0.10, "BNTECHGO kit pricing"),
    "e-clip + grub + shim": (0.40, 0.10, "e-clip $0.016 (carton); grub/shim est."),
    "adhesives + varnish + sleeving": (0.60, 0.30, "estimate from 50 ml pack prices"),
    "label + packaging": (0.30, 0.20, "estimate"),
}


def plan(
    volume_per_year: float = 5000.0,
    operators: int = 2,
    labour_rate: float = LABOUR_RATE_USD_H,
    cnc_winder_min: float | None = None,
) -> LinePlan:
    equipment_total = sum(e.total for e in EQUIPMENT)
    contingency = CONTINGENCY * equipment_total
    capital = equipment_total + contingency
    stations = list(STATIONS)
    if cnc_winder_min is not None:
        stations = [s if s.key != "S3" else Station(**{**s.__dict__, "labour_min": cnc_winder_min}) for s in stations]
    labour = sum(s.labour_min for s in stations)
    bottleneck = max(stations, key=lambda s: s.labour_min)
    per_operator_day = PRODUCTIVE_MIN_PER_DAY / labour
    capacity = {n: int(per_operator_day * n * WORK_DAYS) for n in (1, 2, 3)}
    unit = {}
    for label, column, qty in (("pilot (50 pcs)", 0, 50), (f"volume ({int(volume_per_year)}/yr)", 1, volume_per_year)):
        bom = sum(v[column] for v in BOM.values())
        lab = labour / 60.0 * labour_rate
        scrap = 0.05 if column == 0 else 0.02
        amort = capital / (3.0 * max(volume_per_year, qty)) if column else capital / max(qty, 1) * 0.0
        unit[label] = {
            "bom_usd": bom,
            "labour_usd": lab,
            "scrap_usd": (bom + lab) * scrap,
            "capital_amortisation_usd": amort,
            "total_usd": (bom + lab) * (1 + scrap) + amort,
        }
    return LinePlan(
        equipment_total=equipment_total,
        contingency=contingency,
        capital_total=capital,
        within_budget=capital <= BUDGET_USD,
        labour_min_per_motor=labour,
        motors_per_operator_day=per_operator_day,
        bottleneck=f"{bottleneck.key} {bottleneck.name} ({bottleneck.labour_min:.1f} min)",
        capacity_per_year_single_shift=capacity,
        unit_cost=unit,
    )


# ---- floor layout (SVG) ----------------------------------------------------------------------
LAYOUT = (
    # key, label, x, y, w, h (metres), zone colour
    ("S1", "S1 Goods-in / inspection", 0.2, 0.2, 1.8, 0.75, "#e8f1fb"),
    ("ST", "Stores (shelving)", 2.2, 0.2, 2.4, 0.6, "#f2f2f2"),
    ("S3", "S2-S3 Stator prep + winding (2 seats)", 0.2, 1.4, 0.75, 1.8, "#fff3e0"),
    ("S4", "S4-S5 Termination + electrical test", 0.2, 3.5, 1.8, 0.75, "#fff3e0"),
    ("S6", "S6 Varnish + oven (vented)", 2.4, 3.5, 1.2, 0.75, "#fde8e8"),
    ("S7", "S7-S8 Base + rotor subassembly (MAGNET ZONE)", 3.9, 3.5, 1.9, 0.75, "#efe6fa"),
    ("S9", "S9 Balancing", 5.05, 2.1, 0.75, 1.1, "#efe6fa"),
    ("S10", "S10 Final assy + EOL test", 5.05, 0.95, 0.75, 1.0, "#e6f6ec"),
    ("ST2", "Thrust stand cage", 4.8, 0.2, 1.0, 0.6, "#e6f6ec"),
    ("S11", "S11 Pack / ship", 3.4, 1.6, 1.2, 0.6, "#f2f2f2"),
)


def layout_svg(width_m: float = 6.0, depth_m: float = 4.5) -> str:
    k = 120.0  # px per metre
    W, H = width_m * k + 40, depth_m * k + 70
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" height="{H:.0f}" font-family="Arial, sans-serif">',
           f'<rect width="{W:.0f}" height="{H:.0f}" fill="white"/>',
           '<defs><marker id="ar" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="#c0392b"/></marker></defs>',
           f'<rect x="20" y="20" width="{width_m * k:.0f}" height="{depth_m * k:.0f}" fill="none" stroke="#333" stroke-width="3"/>']  # fmt: skip
    centres = {}
    for key, label, x, y, w, h, colour in LAYOUT:
        px, py = 20 + x * k, 20 + y * k
        out.append(
            f'<rect x="{px:.0f}" y="{py:.0f}" width="{w * k:.0f}" height="{h * k:.0f}" rx="4" fill="{colour}" stroke="#555"/>'
        )
        words = label.split(" ")
        lines, current = [], ""
        limit = max(int(w * k / 7.0), 8)
        for word in words:
            if len(current) + len(word) + 1 > limit:
                lines.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()
        lines.append(current)
        for i, text in enumerate(lines):
            out.append(
                f'<text x="{px + w * k / 2:.0f}" y="{py + h * k / 2 - 7 * (len(lines) - 1) + 14 * i + 4:.0f}" font-size="12" text-anchor="middle">{text}</text>'
            )
        centres[key] = (px + w * k / 2, py + h * k / 2)
    flow = ["ST", "S1", "S3", "S4", "S6", "S7", "S9", "S10", "S11"]
    for a, b in zip(flow, flow[1:], strict=False):
        (x1, y1), (x2, y2) = centres[a], centres[b]
        out.append(
            f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" stroke="#c0392b" stroke-width="2" stroke-dasharray="6,4" marker-end="url(#ar)" opacity="0.8"/>'
        )
    out.append(
        f'<text x="20" y="{H - 28:.0f}" font-size="13" font-weight="bold">RL2407 pilot cell - {width_m:.0f} m x {depth_m:.1f} m (red = material flow). Magnet zone kept 1.5 m+ from winding/termination.</text>'
    )
    out.append(
        f'<text x="20" y="{H - 10:.0f}" font-size="11">Scale: grid 1 m = {k:.0f} px. Benches 0.75 x 1.8 m (30x72 in). Oven corner vented outside. Thrust stand inside a mesh cage.</text>'
    )
    out.append("</svg>")
    return "".join(out)
