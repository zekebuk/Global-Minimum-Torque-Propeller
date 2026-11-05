"""Candidate suppliers for the bought-in parts and materials.

Researched on 2026-11-05 from supplier pages and search results. ``status``:

* ``page``: the price or offer was read on the supplier or retailer page;
* ``snippet``: from a search-result snippet of that page; not opened, so verify;
* ``quote``: no published price, request a quote;
* ``listing``: a marketplace listing range, not a quote.

Prices exclude shipping, VAT and duty. None of these suppliers have been contacted. Every line is a
candidate to qualify (samples + first-article inspection), not an endorsement.
"""

from __future__ import annotations

from dataclasses import dataclass

SEEN = "2026-11-05"


@dataclass(frozen=True)
class Supplier:
    category: str
    name: str
    url: str
    offer: str
    price: str
    status: str
    notes: str = ""


SUPPLIERS: tuple[Supplier, ...] = (
    # 1. laminations / stacks
    Supplier(
        "laminations",
        "Motorneo (CN)",
        "https://motorneo.com/self-bonding/",
        "0.10-0.35 mm incl. 10JNEX900, 20JNEH; EDM/laser/stamping; Backlack, glue-dot, welding, interlock; epoxy coating 0.15-0.6 mm; winding",
        "quote",
        "quote",
        "One-stop: lams + bonding + coating",
    ),
    Supplier(
        "laminations",
        "PuTian YouYou Technology (CN)",
        "https://www.backlack.com/news.html",
        "0.10 mm 10JNEX900 stock; custom Backlack stators; FPV/UAV customers claimed",
        "quote",
        "quote",
        "Rapid prototyping, stress-relief annealing",
    ),
    Supplier(
        "laminations",
        "JYStator / Jiaye (CN)",
        "https://jystator.com/h-col-144.html",
        "BLDC stator samples (EDM/laser) + B/F/H epoxy coating",
        "quote (MOQ 1 pc, ~15 days per snippet, unverified)",
        "snippet",
    ),
    Supplier(
        "laminations",
        "Polaris Laser Laminations (US)",
        "https://www.polarislaserlaminations.com/laser-cut-stator-laminations/",
        "Laser-cut NO Si-steel, Arnon 5, Hiperco 50; loose/bonded/welded/riveted stacks",
        "quote",
        "quote",
        "Accepts DXF/STEP",
    ),
    Supplier(
        "laminations",
        "Precision Micro (UK)",
        "https://www.precisionmicro.com/components/electric-motor-laminations/",
        "Photo-chemical etching 0.1-1.0 mm, stress-free, +/-0.005-0.020 mm",
        "quote",
        "quote",
        "Best fit for 0.1 mm thin teeth",
    ),
    Supplier(
        "laminations",
        "LCS Company / Tempel (US)",
        "https://www.lcscompany.com/capabilities/electric-motor-laminations/",
        "Laser prototypes; Tempel for later volume stamping",
        "quote",
        "quote",
    ),
    # 2. coating
    Supplier(
        "coating",
        "3M Scotchcast 265 powder (material)",
        "https://www.jamindustrialsupply.com/product/3m-scotchcast-electrical-resin-265-2/",
        "One-part epoxy powder: coating, bonding",
        "$1,508.36 / 50 lb; $29.49-42.93/lb elsewhere",
        "snippet",
        "50 lb case is far beyond pilot needs: prefer vendor coating",
    ),
    Supplier(
        "coating",
        "JYStator (CN)",
        "https://www.jystator.com/h-col-116.html",
        "B/F/H stator epoxy coating service",
        "quote",
        "quote",
    ),
    # 3. magnets
    Supplier(
        "magnets",
        "K&J Magnetics (US)",
        "https://www.kjmagnetics.com/563m04-custom-neodymium-magnet",
        "Custom arcs to drawing from 1 pc (min. order value), 7-9 weeks",
        "quote",
        "quote",
        "Prototype route",
    ),
    Supplier(
        "magnets",
        "Ningbo Bestway Magnet (CN)",
        "https://bwmagnet.en.made-in-china.com/product/JuOpiZsDXxWz/",
        "N52H drone arcs; Ni/custom coating; +/-0.05 thickness std",
        "US$0.022-0.08 / pc listing, MOQ 1,000, 15-25 days",
        "listing",
        "Ask for +/-0.02 mm and flux matching",
    ),
    Supplier(
        "magnets",
        "MPCO Magnets (CN)",
        "https://mpcomagnetics.com/product/n52-neodymium-drone-motor-magnet/",
        "N52..UH drone arcs, NiCuNi/epoxy",
        "quote",
        "quote",
    ),
    Supplier(
        "magnets",
        "Courage Magnet (CN)",
        "https://www.couragemagnet.com/magnet-blog/3578.html",
        "N50H/N52H drone arcs, NiCuNi, prototyping",
        "quote",
        "quote",
    ),
    Supplier(
        "magnets",
        "Arnold Magnetic (US)",
        "https://www.arnoldmagnetics.com/permanent-magnets/",
        "Custom NdFeB, AS9100/ITAR",
        "quote",
        "quote",
        "Non-China supply chain",
    ),
    # 4. wire
    Supplier(
        "wire",
        "BAE Wire (US)",
        "https://www.baewire.com/essex-h200-23-awg-heavy-build-copper-magnet-wire-p/mwh200230cn.htm",
        "Essex H200 23 AWG heavy build 200 C, 10 lb spool",
        "$155.42 / 10 lb",
        "snippet",
    ),
    Supplier(
        "wire",
        "Electromechanics Online",
        "https://www.electromechanicsonline.com/products/MW230SABR.asp",
        "23 AWG 200 C by the pound",
        "$59.63/lb (1-4 lb), $53.46/lb (10-24 lb)",
        "snippet",
    ),
    Supplier(
        "wire",
        "Remington Industries",
        "https://www.remingtonindustries.com/magnet-wire/magnet-wire-by-awg/magnet-wire-23-awg/",
        "23 AWG 200 C heavy build, many spool sizes",
        "$14.31-387.39 range",
        "snippet",
    ),
    Supplier(
        "wire",
        "BNTECHGO (silicone leads)",
        "https://bntechgo.com/",
        "20 AWG silicone 100x0.08 tinned, 200 C 600 V",
        "$17.98 per 5 x 25 ft kit",
        "snippet",
    ),
    # 5. bearings
    Supplier(
        "bearings",
        "EZO 684ZZ - Wych Bearings (UK)",
        "https://www.wychbearings.co.uk/684zz_ezo_miniature_bearing_shielded.html",
        "684ZZ 4x9x4 shielded",
        "GBP 2.98 ex VAT each",
        "snippet",
    ),
    Supplier(
        "bearings",
        "EZO 684ZZ - simmering.pl (EU)",
        "https://simmering.pl/en/deep-groove-ball-bearings/28687-684-zz-ezo-4x9x4-deep-groove-ball-bearing.html",
        "684ZZ 4x9x4",
        "EUR 1.92 net each",
        "snippet",
    ),
    Supplier(
        "bearings",
        "NMB L-940ZZ (= 684ZZ)",
        "https://product.minebeamitsumi.com/en/product/category/bearing/miniature_small/parts/L940ZZ.html",
        "Same bearing, NMB designation",
        "quote via distributor",
        "quote",
        "MR84ZZ is 4x8x3 - NOT interchangeable",
    ),
    Supplier(
        "bearings",
        "NSK 684AZZ1",
        "https://www.nsk.com/engineering/684azz1mc3p6-apn.html",
        "684 class, P6 option",
        "quote",
        "quote",
    ),
    # 6. shafts
    Supplier(
        "shafts",
        "Misumi (custom / economy linear shafts)",
        "https://us.misumi-ec.com/vona2/mech/M0100000000/M0101000000/",
        "Hardened ground shafts; custom configuration",
        "from $2.49 (category page; 4 mm not confirmed)",
        "snippet",
        "Stepped Ø4/Ø5 + groove + thread likely needs custom turn+grind",
    ),
    # 7. CNC
    Supplier(
        "cnc",
        "Xometry",
        "https://www.xometry.com/capabilities/cnc-machining-service/cnc-milling-service/",
        "7075-T6 from STEP, instant quote",
        "instant quote",
        "quote",
    ),
    Supplier(
        "cnc",
        "PCBWay",
        "https://www.pcbway.com/rapid-prototyping/cnc-machining/metal/aluminum/",
        "7075-T6/T651 CNC",
        "instant quote",
        "quote",
    ),
    Supplier(
        "cnc",
        "JLCCNC",
        "https://jlccnc.com/help/article/aluminum-7075-cnc-machining",
        "7075 CNC; 'start at $1'",
        "instant quote",
        "quote",
    ),
    # 8. adhesives / varnish
    Supplier(
        "adhesives",
        "Loctite AA 326 + SF 7649 (magnet bonding)",
        "https://www.ellsworth.com/products/adhesives/anaerobic/henkel-loctite-speedbonder-326-structural-adhesive-50-ml-bottle/",
        "Structural anaerobic + activator",
        "~$33.85 (326); EUR 15.99 / 150 ml (7649)",
        "snippet",
    ),
    Supplier(
        "adhesives",
        "Loctite 638 / 648 (retaining)",
        "https://www.ellsworth.com/products/adhesives/anaerobic/henkel-loctite-648-retaining-compound-press-fit-green-50-ml-bottle/",
        "Retaining compound for ring/stator/bearing",
        "648: $66.61 / 50 ml; 638: ~$48.83 / 50 ml",
        "snippet",
    ),
    Supplier(
        "adhesives",
        "Permabond ES5516",
        "https://permabond.com/permabond-introduces-high-temperature-magnet-adhesive-es5516/",
        "High-temp one-part epoxy for magnets",
        "quote",
        "quote",
    ),
    Supplier(
        "adhesives",
        "Elantas Elmotherm 2340/70 (varnish to 220 C)",
        "https://shop.seipp-gmbh.de/en-de/products/impregnating-varnish-elmotherm-2340-70",
        "Impregnating varnish",
        "not captured",
        "quote",
        "Elmotherm 009-0008 is air-dry alkyd - check class",
    ),
    # 9. fasteners
    Supplier(
        "fasteners",
        "Fastener Superstore (DIN 6799 M3.2)",
        "https://www.fastenersuperstore.com/products/605429/e-style",
        "E-clip for 4-5 mm shafts (groove 3.2 h11, 0.64 wide)",
        "~$0.016 each (carton 10,000)",
        "snippet",
    ),
    Supplier(
        "fasteners",
        "McMaster-Carr (DIN 6799, grub screws, shims)",
        "https://www.mcmaster.com/products/din-6799-retaining-rings/",
        "E-clips, M3 cup-point grubs, shim washers",
        "not captured",
        "quote",
    ),
)


def by_category() -> dict[str, list[Supplier]]:
    out: dict[str, list[Supplier]] = {}
    for s in SUPPLIERS:
        out.setdefault(s.category, []).append(s)
    return out


RFQ_QUESTIONS = (
    "Laminations: price/lead time for 75 x 0.10 mm 10JNEX900 (or 20JNEH) per stack at 10-50 stacks and 5,000; Backlack vs glue-dot vs weld; photo-etch vs laser/EDM; stress-relief anneal included? Coating in-house?",
    "Magnets: MOQ and unit price for the 14-pole arc (DWG-004) in N52 vs N48H/N45SH at +/-0.02 mm thickness; magnetisation into -01/-02 variants; per-piece flux data.",
    "Coating: epoxy coat 10-50 stators with masked bore (if not done by the lamination vendor).",
    "CNC: bell (DWG-006) and base (DWG-007) at qty 50 and 500, 7075-T6, anodised, with the drawn tolerances.",
    "Shaft (DWG-008): custom turned, hardened, ground Ø4 h5/Ø5 k6 with DIN 6799 groove, flat and M5 thread; 684ZZ (EZO/NMB) at 1,000-10,000 pcs from an authorised distributor.",
)
