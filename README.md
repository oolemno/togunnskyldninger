# togunnskyldninger

Samler inn hver eneste avviksmelding fra norske togselskaper, og trekker ut
grunnen de oppgir.

SIRI-standarden som Entur bruker har et helt apparat av strukturerte
årsakskoder. Togselskapene bruker ingen av dem — hver melding kommer med
`<UndefinedReason/>`. Hele årsaken ligger i fritekst, i en fast konstruksjon:

> «Dette skyldes at **vi mangler et togsett**.»
> «Toget er innstilt mellom Larvik og Skien. Det skyldes at **toget er forsinket**.»

Det er den setningen dette repoet høster.

## Hvorfor et repo og ikke bare et API-kall

Entur viser bare situasjoner som gjelder **akkurat nå**. Det finnes ikke noe
arkiv, og ingen fører statistikk over hva togselskapene faktisk oppgir som
grunn. Arkivet er noe som må bygges, ikke noe som kan hentes.

Derfor er git-historikken selve produktet: hver commit er et tidsstempel på
når en melding først dukket opp. Dag én er tynn. Etter tre måneder finnes
det ikke maken.

## Hva som samles

| Fil | Innhold |
|---|---|
| `data/ÅÅÅÅ-MM.jsonl` | Én linje per unike melding, den måneden den ble sett |
| `sammendrag.json` | Opptelling av hele arkivet |

Feltene per melding: situasjonsnummer, versjon, selskap, gyldighetsperiode,
tittel og beskrivelse på norsk **og** engelsk (togselskapene leverer begge),
prioritet, rapporttype — pluss to utledede:

- **`aarsak`** — det normaliserte årsaksleddet, trukket ut med regex fra
  «skyldes at …», «skyldes …», «på grunn av …», «grunnet …».
- **`vogner_faktisk` / `vogner_planlagt`** — fra meldinger av typen «kjører
  dessverre med 4 vogner i stedet for 8 vogner». Summen av differansen er
  `tapte_vogner` i sammendraget: hvor mange vogner Norge har manglet siden
  målingen startet.

Store `Affects`-lister lagres som antall, ikke innhold. Én planlagt
vedlikeholdsmelding kan berøre hundrevis av avganger, og repoet ville ellers
vokst fortere enn dataen er verdt.

## Kilde og lisens

[Entur SIRI SX](https://developer.entur.org/pages-real-time-api/), endepunkt
`https://api.entur.io/realtime/v1/rest/sx?datasetId=…`

Gratis, ingen API-nøkkel, ingen registrering. Entur ber om at klienter
identifiserer seg med `ET-Client-Name` — uidentifiserte konsumenter blir
strupet. Oppgitt grense er **4 kall i minuttet**; scriptet venter 16 sekunder
mellom hvert selskap.

Dataen er **NLOD**-lisensiert. Attribusjon til Entur og togselskapene.

Selskaper som hentes: Vy (`NSB`), Go-Ahead (`GOA`), SJ Nord (`SJN`),
Flytoget (`FLT`), Vy Gjøvikbanen (`GJB`), Vy Group (`VYG`), Bane NOR (`BNR`).

## Kjøre lokalt

```bash
python3 samle.py          # ingen avhengigheter, bare stdlib
python3 test_samle.py     # tester mot ekte data hentet 09.09.2026
```

## Ting som er verdt å vite

**Deduplisering.** Nøkkelen er `situasjonsnummer@versjon`. Situasjonsnumre er
globalt unike (`NSB:SituationNumber:…`), så samme melding som dukker opp under
to selskapskoder telles én gang. Når et selskap reviderer en melding lagres den
nye versjonen som en ny rad — revisjonene er en del av historien — men
tellingene i `sammendrag.json` bruker bare siste versjon per situasjon. Ellers
ville en melding som ble revidert fem ganger blitt til fem tapte togsett.

**Tomme commits.** `sammendrag.json` skrives bare når innholdet faktisk endrer
seg. Uten det ville tidsstempelet alene gitt en commit hvert tiende minutt,
døgnet rundt, og git-historikken — som *er* arkivet — ville druknet i støy.

**Offentlig repo er et bevisst valg.** GitHub runder hver Actions-kjøring opp
til ett helt minutt. På et privat repo ville 4 320 kjøringer i måneden sprengt
gratiskvoten på 2 000 minutter. Offentlige repo har ingen grense.

**Schedule kan sovne.** GitHub deaktiverer planlagte workflows i repo uten
aktivitet på 60 dager. Dette repoet committer selv, men hvis innsamlingen
stopper opp uten grunn: sjekk om workflowen er deaktivert i Actions-fanen.

**Go-Ahead er mindre interessant enn Vy.** Deres meldinger er stort sett
ordrikt planlagt vedlikehold med enorme lister over berørte avganger. De
korte, direkte, årsaksbærende meldingene kommer fra Vy.

## Kontekst det er verdt å ha

Bane NORs egen fordeling av forsinkelsesårsaker for 2024: trafikkavvikling
39 %, infrastruktur 28 %, togselskap 17 %, hendelser 16 %. «Hendelser» — flom,
ras, snøstorm, folk i sporet — er altså bare en sekstendel. Dyr i sporet er
sjeldnere enn folkloren skal ha det til.
[Kilde](https://www.banenor.no/nyheter-og-aktuelt/bane-nor-forklarer/hvorfor-er-toget-forsinket/)
