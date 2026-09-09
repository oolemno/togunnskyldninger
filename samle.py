#!/usr/bin/env python3
"""
Samler inn avviksmeldinger fra norske togselskaper via Enturs SIRI SX-endepunkt.

Bakgrunn: SIRI-standarden har et helt apparat av strukturerte arsakskoder.
Vy bruker ingen av dem - hver melding har <UndefinedReason/>. Hele arsaken
ligger i fritekst, i en fast konstruksjon: "Dette skyldes at ...".
Det er den vi hoster.

API-et viser bare situasjoner som gjelder NA. Det finnes ikke noe arkiv.
Derfor dette scriptet: hver kjoring lagrer nye, unike meldinger til en
manedlig JSONL-fil. Arkivet er noe vi bygger, ikke noe vi kan hente.

Data: Entur SIRI SX, lisens NLOD.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

# --- Konfigurasjon ----------------------------------------------------------

BASE_URL = "https://api.entur.io/realtime/v1/rest/sx"

# Kun togselskaper. Kodene er Enturs "codespace"-ID-er.
CODESPACES = [
    ("NSB", "Vy"),
    ("GOA", "Go-Ahead (Sørtoget)"),
    ("SJN", "SJ Nord"),
    ("FLT", "Flytoget"),
    ("GJB", "Vy Gjøvikbanen"),
    ("VYG", "Vy Group"),
    ("BNR", "Bane NOR"),
]

# Entur ber om at klienter identifiserer seg. Uidentifiserte konsumenter
# blir strupet eller blokkert.
CLIENT_NAME = os.environ.get("ET_CLIENT_NAME", "parole-togunnskyldninger")

# Entur oppgir 4 kall per minutt pa dette endepunktet. 16 sekunder mellom
# hvert kall gir god margin.
SLEEP_BETWEEN = float(os.environ.get("SLEEP_BETWEEN", "16"))
TIMEOUT = 60

SIRI_NS = "{http://www.siri.org.uk/siri}"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

DATA_DIR = Path(__file__).parent / "data"

# --- Uttrekk av arsak -------------------------------------------------------

# Rekkefolgen betyr noe: forste treff vinner. "skyldes at X" for "skyldes X".
AARSAK_MONSTRE = [
    re.compile(r"(?:dette|det)\s+skyldes\s+at\s+(.+?)(?:\.|$)", re.IGNORECASE | re.DOTALL),
    re.compile(r"skyldes\s+at\s+(.+?)(?:\.|$)", re.IGNORECASE | re.DOTALL),
    re.compile(r"(?:dette|det)\s+skyldes\s+(.+?)(?:\.|$)", re.IGNORECASE | re.DOTALL),
    re.compile(r"skyldes\s+(.+?)(?:\.|$)", re.IGNORECASE | re.DOTALL),
    re.compile(r"(?:pa|på)\s+grunn\s+av\s+(.+?)(?:\.|$)", re.IGNORECASE | re.DOTALL),
    re.compile(r"(?:grunnet)\s+(.+?)(?:\.|$)", re.IGNORECASE | re.DOTALL),
    # Planlagt arbeid oppgir grunnen som en aktiv setning, ikke med "skyldes":
    # "Bane NOR utforer vedlikeholdsarbeid."
    re.compile(r"utf(?:o|ø)rer\s+(vedlikeholdsarbeid|arbeid)", re.IGNORECASE),
]

# Hva meldingen gjor mot den reisende. Rekkefolgen betyr noe: "Toget er
# innstilt ... Det skyldes at toget er forsinket" skal klassifiseres som
# innstilt, ikke forsinket.
PAAVIRKNINGER = [
    ("innstilt", re.compile(r"\binnstilt\b", re.IGNORECASE)),
    ("faerre_vogner", re.compile(r"vogn(?:er)?\s+i\s+stedet\s+for|færre\s+vogner", re.IGNORECASE)),
    ("buss_for_tog", re.compile(r"buss\s+for\s+tog|setter\s+opp\s+buss|kjører\s+buss", re.IGNORECASE)),
    ("forsinket", re.compile(r"\bforsinke(?:t|lser)\b", re.IGNORECASE)),
]


def klassifiser(beskrivelse: str | None, tittel: str | None = None) -> str | None:
    """
    Hva meldingen faktisk gjor mot den reisende, eller None hvis den bare
    informerer ("Ta andre tog fra Skoyen", "Heisen er ute av drift",
    "Toget kjorer igjen etter tidligere stans").
    """
    tekst = " ".join(t for t in (tittel, beskrivelse) if t)
    if not tekst:
        return None
    for navn, monster in PAAVIRKNINGER:
        if monster.search(tekst):
            return navn
    return None

# "kjorer dessverre med 4 vogner i stedet for 8 vogner"
VOGN_MONSTER = re.compile(
    r"med\s+(\d+)\s+vogn(?:er)?\s+i\s+stedet\s+for\s+(\d+)\s+vogn(?:er)?",
    re.IGNORECASE,
)


def hent_aarsak(beskrivelse: str | None) -> str | None:
    """Trekker ut arsaksleddet fra en norsk avviksbeskrivelse."""
    if not beskrivelse:
        return None
    for monster in AARSAK_MONSTRE:
        treff = monster.search(beskrivelse)
        if treff:
            return rydd(treff.group(1))
    return None


def rydd(tekst: str) -> str:
    """Normaliserer et arsaksledd sa like arsaker teller likt."""
    tekst = re.sub(r"\s+", " ", tekst).strip()
    tekst = tekst.rstrip(" .,;:!?")
    tekst = re.sub(r"^(at|av)\s+", "", tekst, flags=re.IGNORECASE)
    return tekst.lower()


def hent_vogner(beskrivelse: str | None) -> tuple[int, int] | None:
    """Returnerer (faktiske vogner, planlagte vogner) hvis meldingen sier det."""
    if not beskrivelse:
        return None
    treff = VOGN_MONSTER.search(beskrivelse)
    if not treff:
        return None
    faktisk, planlagt = int(treff.group(1)), int(treff.group(2))
    if planlagt < faktisk or planlagt == 0:
        return None
    return faktisk, planlagt


# --- Parsing ----------------------------------------------------------------

def _tekst(node, sti: str) -> str | None:
    funn = node.find(sti)
    if funn is None or funn.text is None:
        return None
    return funn.text.strip() or None


def _sprak(node, tag: str) -> dict[str, str]:
    """Henter alle <Tag xml:lang="..."> under node som en dict."""
    ut: dict[str, str] = {}
    for el in node.findall(f"{SIRI_NS}{tag}"):
        if el.text is None:
            continue
        lang = (el.get(XML_LANG) or "NO").upper()
        ut[lang] = el.text.strip()
    return ut


def parse_situasjoner(xml_tekst: str, codespace: str) -> list[dict]:
    """Gjor et SIRI SX-svar om til flate poster."""
    rot = ET.fromstring(xml_tekst)
    poster = []

    for el in rot.iter(f"{SIRI_NS}PtSituationElement"):
        summary = _sprak(el, "Summary")
        description = _sprak(el, "Description")
        advice = _sprak(el, "Advice")

        beskrivelse_no = description.get("NO")
        aarsak = hent_aarsak(beskrivelse_no)
        vogner = hent_vogner(beskrivelse_no)

        gyldighet = el.find(f"{SIRI_NS}ValidityPeriod")
        start = slutt = None
        if gyldighet is not None:
            start = _tekst(gyldighet, f"{SIRI_NS}StartTime")
            slutt = _tekst(gyldighet, f"{SIRI_NS}EndTime")

        reiser = el.findall(f".//{SIRI_NS}DatedVehicleJourneyRef")
        stopp = el.findall(f".//{SIRI_NS}StopPointRef")

        post = {
            "codespace": codespace,
            "situasjonsnummer": _tekst(el, f"{SIRI_NS}SituationNumber"),
            "versjon": _tekst(el, f"{SIRI_NS}Version"),
            "opprettet": _tekst(el, f"{SIRI_NS}CreationTime"),
            "deltaker": _tekst(el, f"{SIRI_NS}ParticipantRef"),
            "framdrift": _tekst(el, f"{SIRI_NS}Progress"),
            "rapporttype": _tekst(el, f"{SIRI_NS}ReportType"),
            "alvorlighet": _tekst(el, f"{SIRI_NS}Severity"),
            "prioritet": _tekst(el, f"{SIRI_NS}Priority"),
            "planlagt": (_tekst(el, f"{SIRI_NS}Planned") or "").lower() == "true",
            "gyldig_fra": start,
            "gyldig_til": slutt,
            "tittel_no": summary.get("NO"),
            "tittel_en": summary.get("EN"),
            "beskrivelse_no": beskrivelse_no,
            "beskrivelse_en": description.get("EN"),
            "rad_no": advice.get("NO"),
            "aarsak": aarsak,
            "paavirkning": klassifiser(beskrivelse_no, summary.get("NO")),
            "vogner_faktisk": vogner[0] if vogner else None,
            "vogner_planlagt": vogner[1] if vogner else None,
            # Affects-listene kan ha hundrevis av elementer ved planlagt
            # vedlikehold. Vi lagrer antall, ikke hele lista, ellers eksploderer
            # repoet. De forste fem holder for a se hva som er beroert.
            "antall_beroerte_reiser": len(reiser),
            "antall_beroerte_stopp": len(stopp),
            "beroerte_stopp": [s.text for s in stopp[:5] if s.text],
            "forst_sett": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

        if not post["situasjonsnummer"]:
            continue
        poster.append(post)

    return poster


def noekkel(post: dict) -> str:
    """Unik nokkel per melding. Versjon mangler hos noen selskaper."""
    return f"{post['situasjonsnummer']}@{post.get('versjon') or '-'}"


# --- Henting ----------------------------------------------------------------

def hent(codespace: str) -> str:
    url = f"{BASE_URL}?datasetId={codespace}"
    req = urllib.request.Request(
        url,
        headers={
            "ET-Client-Name": CLIENT_NAME,
            "Accept": "application/xml",
            "Accept-Encoding": "gzip",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as svar:
        rad = svar.read()
        if svar.headers.get("Content-Encoding") == "gzip":
            rad = gzip.decompress(rad)
    return rad.decode("utf-8")


# --- Lagring ----------------------------------------------------------------

def les_sette_noekler(maaneder: int = 2) -> set[str]:
    """
    Leser eksisterende noklene fra de siste manedsfilene, sa vi ikke lagrer
    duplikater. Vi leser fra fil framfor a fore en egen state-fil - da kan
    arkivet aldri komme ut av synk med seg selv.
    """
    noekler: set[str] = set()
    filer = sorted(DATA_DIR.glob("*.jsonl"))[-maaneder:]
    for fil in filer:
        with fil.open(encoding="utf-8") as f:
            for linje in f:
                linje = linje.strip()
                if not linje:
                    continue
                try:
                    noekler.add(noekkel(json.loads(linje)))
                except (json.JSONDecodeError, KeyError):
                    continue
    return noekler


def skriv(poster: list[dict]) -> Path:
    maaned = datetime.now(timezone.utc).strftime("%Y-%m")
    fil = DATA_DIR / f"{maaned}.jsonl"
    fil.parent.mkdir(parents=True, exist_ok=True)
    with fil.open("a", encoding="utf-8") as f:
        for post in poster:
            f.write(json.dumps(post, ensure_ascii=False) + "\n")
    return fil


def bygg_sammendrag() -> dict:
    """
    Teller opp hele arkivet pa nytt. Billig nok, og alltid korrekt.

    Viktig: arkivet lagrer hver VERSJON av en melding, fordi revisjonene er
    en del av historien. Men i tellingene skal hver SITUASJON telles en gang -
    ellers blir en melding som Vy reviderte fem ganger til fem tapte togsett.
    Vi beholder derfor siste versjon per situasjonsnummer.
    """
    siste_versjon: dict[str, dict] = {}
    rader = 0
    forste = None
    siste = None

    def versjonsnr(post: dict) -> int:
        try:
            return int(post.get("versjon") or 0)
        except (TypeError, ValueError):
            return 0

    for fil in sorted(DATA_DIR.glob("*.jsonl")):
        with fil.open(encoding="utf-8") as f:
            for linje in f:
                linje = linje.strip()
                if not linje:
                    continue
                try:
                    post = json.loads(linje)
                except json.JSONDecodeError:
                    continue
                rader += 1
                sett = post.get("forst_sett")
                if sett:
                    forste = min(forste, sett) if forste else sett
                    siste = max(siste, sett) if siste else sett
                nr = post.get("situasjonsnummer")
                if not nr:
                    continue
                if nr not in siste_versjon or versjonsnr(post) >= versjonsnr(siste_versjon[nr]):
                    siste_versjon[nr] = post

    uplanlagt: dict[str, int] = {}
    planlagt: dict[str, int] = {}
    per_selskap: dict[str, int] = {}
    per_paavirkning: dict[str, int] = {}
    uten_grunn: dict[str, int] = {}
    tapte_vogner = 0
    kun_informasjon = 0

    for post in siste_versjon.values():
        cs = post.get("codespace") or "?"
        per_selskap[cs] = per_selskap.get(cs, 0) + 1

        if post.get("vogner_planlagt") and post.get("vogner_faktisk"):
            tapte_vogner += post["vogner_planlagt"] - post["vogner_faktisk"]

        aarsak = post.get("aarsak")
        if aarsak:
            # Planlagt vedlikehold og akutte unnskyldninger hoerer ikke hjemme
            # i samme bunke. "Bane NOR utfoerer vedlikeholdsarbeid" er ikke
            # samme sak som "vi mangler et togsett".
            bunke = planlagt if post.get("planlagt") else uplanlagt
            bunke[aarsak] = bunke.get(aarsak, 0) + 1

        # Utledes pa nytt her framfor a leses fra posten, sa hele arkivet -
        # ogsa rader lagret for denne klassifiseringen fantes - telles likt.
        paavirkning = klassifiser(post.get("beskrivelse_no"), post.get("tittel_no"))
        if not paavirkning:
            kun_informasjon += 1
            continue
        per_paavirkning[paavirkning] = per_paavirkning.get(paavirkning, 0) + 1

        # Det interessante tallet: meldinger som forteller den reisende at noe
        # er galt, uten a si hvorfor. Planlagt arbeid holdes utenfor - der er
        # grunnen kjent selv om den ikke star som en "skyldes"-setning.
        if not aarsak and not post.get("planlagt"):
            uten_grunn[paavirkning] = uten_grunn.get(paavirkning, 0) + 1

    return {
        "oppdatert": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "antall_situasjoner": len(siste_versjon),
        "antall_rader": rader,
        "samler_siden": forste,
        "sist_sett": siste,
        "tapte_vogner": tapte_vogner,
        "uten_oppgitt_grunn": sum(uten_grunn.values()),
        "kun_informasjon": kun_informasjon,
        "per_selskap": dict(sorted(per_selskap.items(), key=lambda x: -x[1])),
        "paavirkning": dict(sorted(per_paavirkning.items(), key=lambda x: -x[1])),
        "uten_grunn_per_paavirkning": dict(sorted(uten_grunn.items(), key=lambda x: -x[1])),
        "arsaker_uplanlagt": dict(sorted(uplanlagt.items(), key=lambda x: -x[1])),
        "arsaker_planlagt": dict(sorted(planlagt.items(), key=lambda x: -x[1])),
    }


def uendret(sti: Path, nytt: dict) -> bool:
    """Sant hvis fila finnes og er lik `nytt` naar vi ser bort fra tidsstempel."""
    if not sti.exists():
        return False
    try:
        gammelt = json.loads(sti.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return {k: v for k, v in gammelt.items() if k != "oppdatert"} == {
        k: v for k, v in nytt.items() if k != "oppdatert"
    }


# --- Hovedlop ---------------------------------------------------------------

def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    sett = les_sette_noekler()
    nye: list[dict] = []
    feil = 0

    for i, (codespace, navn) in enumerate(CODESPACES):
        if i:
            time.sleep(SLEEP_BETWEEN)
        try:
            xml_tekst = hent(codespace)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            print(f"  ! {codespace} ({navn}): {e}", file=sys.stderr)
            feil += 1
            continue

        try:
            poster = parse_situasjoner(xml_tekst, codespace)
        except ET.ParseError as e:
            print(f"  ! {codespace} ({navn}): kunne ikke parse XML: {e}", file=sys.stderr)
            feil += 1
            continue

        ferske = [p for p in poster if noekkel(p) not in sett]
        for p in ferske:
            sett.add(noekkel(p))
        nye.extend(ferske)
        print(f"  {codespace:4} {navn:22} {len(poster):4} aktive, {len(ferske):3} nye")

    if feil == len(CODESPACES):
        print("Alle kall feilet. Avbryter uten a skrive.", file=sys.stderr)
        return 1

    if nye:
        fil = skriv(nye)
        print(f"\nSkrev {len(nye)} nye meldinger til {fil.name}")
        for p in nye:
            if p.get("aarsak"):
                print(f"  - {p['aarsak']}")
    else:
        print("\nIngen nye meldinger.")

    # Sammendraget skrives bare nar noe faktisk har endret seg. Ellers ville
    # tidsstempelet alene gitt en commit hvert tiende minutt, dognet rundt,
    # og git-historikken - som ER arkivet - ville druknet i stoy.
    sammendrag = bygg_sammendrag()
    sti = DATA_DIR.parent / "sammendrag.json"
    if not uendret(sti, sammendrag):
        sti.write_text(
            json.dumps(sammendrag, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(
        f"Arkiv: {sammendrag['antall_situasjoner']} situasjoner "
        f"({sammendrag['antall_rader']} rader), "
        f"{len(sammendrag['arsaker_uplanlagt'])} unike uplanlagte arsaker, "
        f"{sammendrag['uten_oppgitt_grunn']} uten oppgitt grunn, "
        f"{sammendrag['tapte_vogner']} tapte vogner."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
