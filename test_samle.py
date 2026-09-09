#!/usr/bin/env python3
"""Tester parsing og arsaksuttrekk mot ekte data hentet 09.09.2026."""

import json
import sys
from pathlib import Path

import samle

FIXTURE = Path(__file__).parent / "fixtures" / "nsb-sample.xml"

feil = 0


def sjekk(navn, faktisk, forventet):
    global feil
    if faktisk == forventet:
        print(f"  ok   {navn}")
    else:
        feil += 1
        print(f"  FEIL {navn}\n       fikk:      {faktisk!r}\n       forventet: {forventet!r}")


def main():
    poster = samle.parse_situasjoner(FIXTURE.read_text(encoding="utf-8"))
    etter_nr = {p["situasjonsnummer"].split(":")[-1][:8]: p for p in poster}

    print("Parsing")
    sjekk("antall situasjoner", len(poster), 5)
    sjekk("noekler er unike", len({samle.noekkel(p) for p in poster}), 5)

    print("\nArsaksuttrekk")
    sjekk(
        "kjoretoy sperrer sporet",
        etter_nr["9e9a90b0"]["aarsak"],
        "et kjøretøy sperrer sporet",
    )
    sjekk(
        "den sirkulaere",
        etter_nr["2598017d"]["aarsak"],
        "toget er forsinket",
    )
    sjekk(
        "mangler togsett",
        etter_nr["47b6027c"]["aarsak"],
        "vi mangler et togsett",
    )
    sjekk("heis uten arsak", etter_nr["3cf1126c"]["aarsak"], None)

    print("\nVognuttrekk")
    sjekk("4 av 8", (etter_nr["9e9a90b0"]["vogner_faktisk"], etter_nr["9e9a90b0"]["vogner_planlagt"]), (4, 8))
    sjekk("5 av 10", (etter_nr["47b6027c"]["vogner_faktisk"], etter_nr["47b6027c"]["vogner_planlagt"]), (5, 10))
    sjekk("ingen vogner nevnt", etter_nr["2598017d"]["vogner_faktisk"], None)

    print("\nFelter som mangler hos noen selskaper")
    goa = [p for p in poster if p["deltaker"] == "GOA"][0]
    sjekk("GOA uten <Version>", goa["versjon"], None)
    sjekk("GOA uten <Progress>", goa["framdrift"], None)
    sjekk("GOA planlagt=True", goa["planlagt"], True)
    sjekk("GOA alvorlighet", goa["alvorlighet"], "noImpact")

    print("\nStore Affects-lister lagres som antall, ikke innhold")
    sjekk("GOA beroerte reiser", goa["antall_beroerte_reiser"], 3)
    sjekk("innstilt: 2 beroerte stopp", etter_nr["2598017d"]["antall_beroerte_stopp"], 2)

    print("\nNormalisering")
    sjekk("stripper punktum og at", samle.rydd("  At Toget Er  Forsinket. "), "toget er forsinket")
    sjekk("uten treff", samle.hent_aarsak("Heisen er ute av drift."), None)
    sjekk("tom input", samle.hent_aarsak(None), None)
    sjekk("skyldes uten at", samle.hent_aarsak("Dette skyldes tekniske problemer."), "tekniske problemer")
    sjekk("paa grunn av", samle.hent_aarsak("Innstilt på grunn av dyr i sporet."), "dyr i sporet")
    sjekk("bakvendt vognantall ignoreres", samle.hent_vogner("med 8 vogner i stedet for 4 vogner"), None)

    print("\nSerialiserbart")
    try:
        json.dumps(poster, ensure_ascii=False)
        print("  ok   alle poster er gyldig JSON")
    except TypeError as e:
        print(f"  FEIL JSON: {e}")
        return 1

    print()
    if feil:
        print(f"{feil} test(er) feilet.")
        return 1
    print("Alle tester passerte.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
