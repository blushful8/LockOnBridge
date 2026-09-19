"""War Thunder UI label aliases for every official game language.

Windows OCR may return correct script OR Latin lookalikes when the matching
OCR language pack is missing — both forms are listed where useful.
"""

from __future__ import annotations

import re

RP_PHRASES: tuple[str, ...] = (
    r"research\s*points?",
    r"researc\w*\s*point\w*",
    r"points?\s*de\s*recherche",
    r"punti\s*ricerca",
    r"forschungspunkte?",
    r"puntos?\s*de\s*investigaci[oó]n",
    r"очк(?:и|ов|а)?\s*исслед\w*",
    r"исслед\w*",
    r"punkty?\s*bada[nń]",
    r"v[yý]zkumn[eé]\s*bod\w*",
    r"vyzkumne\s*bod\w*",
    r"ara[sş]t[iı]rma\s*puan\w*",
    r"arastirma\s*puan\w*",
    r"fejlesztesi\s*pontok?",
    r"研发点",
    r"研發點數",
    r"研發点",
    r"リサーチポイント",
    r"リサーチ\s*ポイント",
    r"pontos?\s*de\s*pesquisa",
    r"очк(?:и|ів|а)?\s*дослідж\w*",
    r"дослідж\w*",
    r"poeni\s*za\s*istra[zž]ivanje",
    r"fejleszt[eé]si\s*pontok?",
    r"연구\s*점수",
    r"пункты?\s*даследавання[уў]?",
    r"punkty?\s*dasled\w*",
    r"puncte\s*de\s*cercetare",
    r"[dđ]i[eể]m\s*nghi[eê]n\s*c[uứ]u",
    # Latinized Cyrillic OCR (UK/RU UI + EN pack)
    r"ochk\w*\s*(?:issled|doslid|nocnin)\w*",
    r"dos[l1i]id\w*",
    r"oc[qc]r?\w*\s*nocni",
    r"issled\w*",
    r"\br\.?\s*p\.?\b",
    r"\brp\b",
    r"\bfp\b",
    r"\bpb\b",
)

SL_PHRASES: tuple[str, ...] = (
    r"silver\s*lions?",
    r"silve\w*\s*lion\w*",
    r"lions?\s*d['’]?argent",
    r"leoni\s*d['’]?argento",
    r"silberne?\s*l[oö]wen?",
    r"leones?\s*de\s*plata",
    r"серебрян\w*\s*льв\w*",
    r"серебрян\w*",
    r"льв(?:ы|ов|а)?",
    r"srebrn\w*\s*lw\w*",
    r"st[rř][ií]brn\w*\s*lv\w*",
    r"stribrn\w*\s*lv\w*",
    r"g[uü]m[uü][sş]\s*aslan\w*",
    r"gumus\s*aslan\w*",
    r"ezust\s*oroszl[aá]?n\w*",
    r"银狮",
    r"銀獅",
    r"シルバーライオン",
    r"シルバー\s*ライオン",
    r"срібн\w*\s*лев\w*",
    r"срібн\w*",
    r"srebrn\w*\s*lav\w*",
    r"ez[uü]st\s*oroszl[aá]n\w*",
    r"실버\s*라이온",
    r"срэбн\w*\s*(?:льв|ільв)\w*",
    r"srebny\w*\s*lv\w*",
    r"lei\s*de\s*argint",
    r"sri[b6]\w*",
    r"serebr\w*",
    r"l[eе]v(?:y|i|iv)?",
    r"\bs\.?\s*l\.?\b",
    r"\bsl\b",
)

WITH_PREMIUM_PHRASES: tuple[str, ...] = (
    r"with\s*premium",
    r"avec\s*premium",
    r"con\s*premium",
    r"mit\s*premium",
    r"с\s*премиум\w*",
    r"з\s*преміум\w*",
    r"з\s*прэміум\w*",
    r"z\s*premium",
    r"s\s*premium",
    r"premium\s*ile",
    r"premiummal",
    r"cu\s*premium",
    r"com\s*premium",
    r"sa\s*premium",
    r"프리미엄\s*포함",
    r"プレミアム\s*あり",
    r"有\s*高级账号",
    r"有\s*高級帳號",
    r"[3zсЗ]\s*npe?[mn]i?[yуu0о]?m\w*",
)

WITHOUT_PREMIUM_PHRASES: tuple[str, ...] = (
    r"without\s*premium",
    r"w/?o\s*premium",
    r"sans\s*premium",
    r"senza\s*premium",
    r"ohne\s*premium",
    r"sin\s*premium",
    r"без\s*премиум\w*",
    r"без\s*преміум\w*",
    r"без\s*прэміум\w*",
    r"bez\s*premium",
    r"sem\s*premium",
    r"premium\s*olmadan",
    r"premium\s*n[eé]lk[uü]l",
    r"premium\s*nelkul",
    r"f[aă]r[aă]\s*premium",
    r"fara\s*premium",
    r"프리미엄\s*없음",
    r"プレミアム\s*なし",
    r"无\s*高级账号",
    r"無\s*高級帳號",
    r"be[zs3]\s*npe?[mn]i?[yуu0о]?m\w*",
    # Hybrid: Cyrillic «без» + Latinized «npeMiYM…» (common when Win OCR pack ≠ UI lang)
    r"без\s*npe?[mn]i?[yуu0о]?m\w*",
    r"без\s*npex?[iyu]\w*",
    # «Без» then OCR inserts «З» before the premium word
    r"без\s*[3zзс]\s*npe?[mn]i?[yуu0о]?m\w*",
    # OCR often turns «Без преміума» into «5B npeMiyxa» / «SB npeMiYMa» / «Ee3 npeMiyxa»
    r"[s5]\s*[bв]\s*npe?[mn]i?[yуux]\w*",
    r"[eе]{1,2}[zs3]\s*npe?[mn]i?[yуux]\w*",
    # «Sea npexiyxa» / «Без npa…» (Win OCR invents spaces / drops letters)
    r"sea\s*npe[xх]?[iyu]\w*",
    r"без\s*npa\w*",
    r"bez\s*npa\w*",
)

VICTORY_PHRASES: tuple[str, ...] = (
    r"victory",
    r"victoire",
    r"vittoria",
    r"sieg",
    r"victoria",
    r"победа",
    r"перемога",
    r"zwyci[eę]stwo",
    r"zwyciestwo",
    r"v[ií]t[eě]zstv[ií]",
    r"vitezstvi",
    r"zafer",
    r"vit[oó]ria",
    r"побед",
    r"peremog",
    r"peramoga",
    r"nepeMor",
    r"gy[oöő]zelem",
    r"gyozelem",
    r"victorie",
    r"pobeda",
    r"перамога",
    r"승리",
    r"勝利",
    r"胜利",
)

DEFEAT_PHRASES: tuple[str, ...] = (
    r"defeat",
    r"d[eé]faite",
    r"sconfitta",
    r"niederlage",
    r"derrota",
    r"поражение",
    r"поразка",
    r"провал\w*",
    r"провален\w*",
    r"npoBaneHa",
    r"przegrana",
    r"por[aá][zž]ka",
    r"yenilgi",
    r"패배",
    r"敗北",
    r"失败",
    r"失敗",
)

REWARD_LINE_PHRASES: tuple[str, ...] = (
    r"reward\s*for\s*(?:victory|winning|win|participation|mission)",
    r"award\s*for\s*(?:victory|winning)",
    r"r[eé]compense\s*(?:de\s*)?victoire",
    r"belohnung\s*f[uü]r\s*(?:sieg|gewinn)",
    r"recompensa\s*por\s*victoria",
    r"награда\s*за\s*побед\w*",
    r"награда\s*за\s*участ\w*",
    r"нагород\w*\s*за\s*перемог\w*",
    r"нагород\w*\s*за\s*участь",
    # Latinized Cyrillic OCR («нагорода» → Haropona / ropona)
    r"(?:h?aropon\w*|ropona)\s*3a\s*nepeMor\w*",
    r"(?:h?aropon\w*|ropona)\s*za\s*nepeMor\w*",
    r"(?:h?aropon\w*|ropona)\s*sanepeM0ty",
    r"(?:h?aropon\w*|ropona)\s*3a\s*yuac",
    r"(?:h?aropon\w*|ropona)\s*za\s*yuac",
)


def _join(phrases: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile("(?:" + "|".join(phrases) + ")", re.IGNORECASE | re.UNICODE)


RP_LABEL = _join(RP_PHRASES)
SL_LABEL = _join(SL_PHRASES)
WITH_PREMIUM = _join(WITH_PREMIUM_PHRASES)
WITHOUT_PREMIUM = _join(WITHOUT_PREMIUM_PHRASES)
VICTORY = _join(VICTORY_PHRASES)
DEFEAT = _join(DEFEAT_PHRASES)
REWARD_LINE = _join(REWARD_LINE_PHRASES)

# Short UI samples for offline self-tests (labels only — amounts are synthetic).
LANGUAGE_FIXTURES: dict[str, dict[str, str]] = {
    "en": {"rp": "Research Points", "sl": "Silver Lions", "with": "With premium", "without": "Without premium", "victory": "Victory"},
    "fr": {"rp": "Points de recherche", "sl": "Silver Lions", "with": "Avec premium", "without": "Sans premium", "victory": "Victoire"},
    "de": {"rp": "Forschungspunkte", "sl": "Silver Lions", "with": "Mit Premium", "without": "Ohne Premium", "victory": "Sieg"},
    "es": {"rp": "Puntos de Investigación", "sl": "Leones de Plata", "with": "Con premium", "without": "Sin premium", "victory": "Victoria"},
    "it": {"rp": "Punti ricerca", "sl": "Leoni d'Argento", "with": "Con premium", "without": "Senza premium", "victory": "Vittoria"},
    "ru": {"rp": "Очки исследований", "sl": "Серебряные львы", "with": "С премиумом", "without": "Без премиума", "victory": "Победа"},
    "uk": {"rp": "Очки досліджень", "sl": "Срібні леви", "with": "З преміумом", "without": "Без преміуму", "victory": "Перемога"},
    "pl": {"rp": "Punkty Badań", "sl": "Srebrne Lwy", "with": "Z premium", "without": "Bez premium", "victory": "Zwycięstwo"},
    "cs": {"rp": "Výzkumné body", "sl": "Stříbrní lvi", "with": "S premium", "without": "Bez premium", "victory": "Vítězství"},
    "pt": {"rp": "Pontos de pesquisa", "sl": "Silver Lions", "with": "Com premium", "without": "Sem premium", "victory": "Vitória"},
    "tr": {"rp": "Araştırma Puanları", "sl": "Gümüş Aslan", "with": "Premium ile", "without": "Premium olmadan", "victory": "Zafer"},
    "ja": {"rp": "リサーチポイント", "sl": "シルバーライオン", "with": "プレミアムあり", "without": "プレミアムなし", "victory": "勝利"},
    "ko": {"rp": "연구 점수", "sl": "실버 라이온", "with": "프리미엄 포함", "without": "프리미엄 없음", "victory": "승리"},
    "zh": {"rp": "研发点", "sl": "银狮", "with": "有高级账号", "without": "无高级账号", "victory": "胜利"},
    "hu": {"rp": "Fejlesztési pontok", "sl": "Ezüst Oroszlánok", "with": "Premiummal", "without": "Premium nélkül", "victory": "Győzelem"},
    "ro": {"rp": "Puncte de cercetare", "sl": "Lei de argint", "with": "Cu premium", "without": "Fără premium", "victory": "Victorie"},
    "be": {"rp": "Пункты даследаванняў", "sl": "Срэбныя львы", "with": "З прэміумам", "without": "Без прэміума", "victory": "Перамога"},
    "sr": {"rp": "Poeni za istraživanje", "sl": "Srebrni lavovi", "with": "Sa premium", "without": "Bez premium", "victory": "Pobeda"},
}

# Wrong/missing OCR pack → diacritics stripped or Cyrillic Latinized (same class of bug as UA).
MANGLED_LANGUAGE_FIXTURES: dict[str, dict[str, str]] = {
    "pl": {
        "rp": "Punkty Badan",
        "sl": "Srebrne Lwy",
        "with": "Z premium",
        "without": "Bez premium",
        "victory": "Zwyciestwo",
    },
    "cs": {
        "rp": "Vyzkumne body",
        "sl": "Stribrni lvi",
        "with": "S premium",
        "without": "Bez premium",
        "victory": "Vitezstvi",
    },
    "tr": {
        "rp": "Arastirma Puanlari",
        "sl": "Gumus Aslan",
        "with": "Premium ile",
        "without": "Premium olmadan",
        "victory": "Zafer",
    },
    "hu": {
        "rp": "Fejlesztesi pontok",
        "sl": "Ezust Oroszlanok",
        "with": "Premiummal",
        "without": "Premium nelkul",
        "victory": "Gyozelem",
    },
    "ro": {
        "rp": "Puncte de cercetare",
        "sl": "Lei de argint",
        "with": "Cu premium",
        "without": "Fara premium",
        "victory": "Victorie",
    },
    "de": {
        "rp": "Forschungspunkte",
        "sl": "Silberne Lowen",
        "with": "Mit Premium",
        "without": "Ohne Premium",
        "victory": "Sieg",
    },
    "fr": {
        "rp": "Points de recherche",
        "sl": "Lions d'argent",
        "with": "Avec premium",
        "without": "Sans premium",
        "victory": "Victoire",
    },
    "be": {
        "rp": "Punkty dasledavannyau",
        "sl": "Srebnyya lvy",
        "with": "Z premiumam",
        "without": "Bez premiuma",
        "victory": "Peramoga",
    },
    "sr": {
        "rp": "Poeni za istrazivanje",
        "sl": "Srebrni lavovi",
        "with": "Sa premium",
        "without": "Bez premium",
        "victory": "Pobeda",
    },
}
