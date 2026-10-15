"""Write wt_debrief_labels.py from the official menu_debriefing.csv dump."""

from __future__ import annotations

import csv
from pathlib import Path

CSV = Path(r"C:\Users\User\.cursor\projects\d-projects-LockOn\agent-tools\e5c4441d-5c99-4155-8860-ff9f2ea19313.txt")
OUT = Path(r"D:\projects\LockOnBridge\lockon_bridge\wt_debrief_labels.py")

# Column index in Gaijin lang CSV → LockOnBridge language code.
COLUMNS = {
    1: "en",
    2: "fr",
    3: "it",
    4: "de",
    5: "es",
    6: "ru",
    7: "pl",
    8: "cs",
    9: "tr",
    10: "zh",
    11: "ja",
    12: "pt",
    13: "uk",
    14: "sr",
    15: "hu",
    16: "ko",
    17: "be",
    18: "ro",
    19: "zh_hant",
}

KEYS = {
    "debriefing/total/unitsResearch": "VEHICLE_RESEARCH",
    "debriefing/total/modsResearch": "MODIFICATION_RESEARCH",
    "debriefing/modResearch": "MODIFICATION_RESEARCH_ROW",
    "debriefing/freeExp": "CONVERTIBLE_RP",
    "debriefing/withPremium": "WITH_PREMIUM",
    "debriefing/withoutPremium": "WITHOUT_PREMIUM",
    "debriefing/nationResearchBonus": "VEHICLE_RESEARCH_BONUS",
}


def main() -> None:
    rows = list(csv.reader(CSV.open(encoding="utf-8"), delimiter=";"))
    found: dict[str, dict[str, str]] = {name: {} for name in KEYS.values()}
    for row in rows:
        if not row:
            continue
        key = row[0].strip().strip('"')
        name = KEYS.get(key)
        if name is None:
            continue
        for index, code in COLUMNS.items():
            if index < len(row) and row[index].strip():
                found[name][code] = row[index]
    lines = [
        '"""Official War Thunder debriefing strings.',
        "",
        "Copied from lang/menu_debriefing.csv (Gaijin localization).",
        "Keys: debriefing/total/unitsResearch and the neighbouring rows that must",
        "not be used as vehicle research. Characters, including word joiners in",
        "Chinese and the tab in Japanese, are kept as in the game file.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
    ]
    for name, mapping in found.items():
        lines.append(f"{name}: dict[str, str] = {{")
        for code, text in mapping.items():
            lines.append(f"    {code!r}: {text!r},")
        lines.append("}")
        lines.append("")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    uk = found["VEHICLE_RESEARCH"]["uk"]
    if uk != "Дослідження техніки":
        raise SystemExit(f"unexpected Ukrainian vehicle row: {uk!r}")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
