# PL:L1 v3 — kalibracja 3 marek

Data: 2026-07-24  
Profil: `PL:L1:v2`  
Ścieżka wykonawcza: `cheap-effective-v3`

## Cel

Warunki dopuszczenia do kontrolowanego skalowania:

- średnio 8–12 propozycji pól na markę;
- średnio 5–8 bezpiecznie opublikowanych pól na markę;
- jawny koszt wszystkich artefaktów w końcowej linii danych;
- publikacja wyłącznie przez Auto-review i Publication Gate.

## Próba

| Marka | Propozycje | Bezpieczne publikacje | Koszt końcowej linii |
|---|---:|---:|---:|
| CleanWhale | 9 | 6 | $0.47756000 |
| Czas na Herbatę | 10 | 7 | $0.51507750 |
| Koku Sushi | 15 | 6 | $0.70030500 |
| **Razem / średnia** | **34 / 11.33** | **19 / 6.33** | **$1.69294250 / $0.56431417** |

Koszt końcowej linii na bezpieczną publikację wyniósł około
`$0.08910`, czyli około `11.22` opublikowanego pola na `$1`.

## Wynik bramek

- bramka propozycji: **spełniona** (`11.33`);
- bramka bezpiecznych publikacji: **spełniona** (`6.33`);
- kompletność 20 pól L1 na każdej marce: **niespełniona**;
- zgoda na duży batch bez dalszego monitoringu: **nie**.

## Opublikowane pola

CleanWhale:

- `brand.name`
- `brand.public_summary`
- `websites.official`
- `contact.generic_business_route`
- `offer.unit_formats`
- `support.training_program`

Czas na Herbatę:

- `brand.name`
- `brand.public_summary`
- `websites.official`
- `contact.generic_business_route`
- `offer.unit_formats`
- `candidate.premises_requirements`
- `support.training_program`

Koku Sushi:

- `brand.public_summary`
- `websites.official`
- `websites.franchise_offer`
- `contact.generic_business_route`
- `candidate.premises_requirements`
- `support.training_program`

## Naprawy wynikające z kalibracji

1. Risk-based Checker ocenia ryzyko na poziomie claimu/pola, a nie całego
   zadania.
2. Do Checkera trafiają tylko źródła cytowane przez oceniane claimy.
3. Normalizer oznacza bezpieczne wartości spoza semantycznego zakresu Checkera
   jako `not_reviewed` i zachowuje proweniencję
   `risk_based_low_risk_normalized`.
4. Workbench potrafi odtworzyć tę proweniencję ze starszych artefaktów.
5. Oficjalny URL i URL oferty franczyzowej są wybierane osobno według roli,
   wyłącznie z oficjalnych źródeł zaobserwowanych przez providera.
6. Opisy są grupowane i ograniczane długością przed Auto-review.

## Koszt prac kalibracyjnych

Końcowy, powtarzalny koszt trzech poprawionych linii artefaktów to
`$1.69294250`. Rzeczywisty koszt kampanii i ponownych wywołań diagnostycznych
tej finalnej próby wyniósł około `$2.75126750`. Wcześniejsza, wstępna próba
diagnostyczna trzech innych marek kosztowała dodatkowo około `$1.47818500`.
Koszt diagnostyczny nie jest prognozą kosztu produkcyjnego.

## Decyzja

Ścieżka spełnia pilotażowe bramki v3. Można przejść do małego,
monitorowanego batcha, np. 10 marek, z automatycznym zatrzymaniem, gdy średnia
spadnie poniżej 8 propozycji lub 5 publikacji na markę albo koszt przekroczy
ustalony limit. Wynik `partial` pozostaje prawidłowym rezultatem L1; braków nie
wolno przedstawiać jako pełnego researchu.
