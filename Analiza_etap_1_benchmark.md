# Etap 1 — wynik benchmarku PL:L1

Data wykonania: 23 lipca 2026  
Profil: `PL:L1:v2`  
Próba: 10 marek × 20 pól = 200 pól  
Kampania pipeline: `59c70f1a-fa9e-45cf-897b-acfe69d4e843`

## 1. Co oznacza „researcher + ChatGPT” w tym eksperymencie

Pierwotna nazwa zakłada pracownika, który sam formułuje rozbudowane pytanie do
ChatGPT, ocenia źródła i przepisuje dane. Użytkownik zlecił wykonanie tej pracy
bez własnego udziału, dlatego zastosowano **AI-assisted direct proxy**:

1. jeden call Responses API z web search na każdą markę;
2. jeden prompt obejmujący zamrożony kontrakt 20 pól PL:L1;
3. brak dostępu do wyników pipeline’u i Gold Setu;
4. wynik `found`, `not_public` albo `not_applicable` dla każdego pola;
5. obowiązkowy URL, typ źródła i data pobrania dla wartości `found`;
6. osobny call review porównujący direct i pipeline z wcześniej zamrożonym
   Goldem.

Jest to wiarygodne porównanie **dwóch sposobów użycia tego samego API**, ale nie
jest eksperymentem z niezależnym pracownikiem. Zmierzone czasy review są
rzeczywistym czasem wall-clock calla audytującego, a nie czasem pracy człowieka.

## 2. Gold Set

Gold powstał w osobnym, zaślepionym przebiegu bez submissionów:

- 200/200 pól ma zakończony status;
- 124 wartości znaleziono;
- 72 oznaczono `not_public`;
- 4 oznaczono `not_applicable`;
- 0 pozostało `pending`;
- 42 unikalne bezpośrednie URL-e źródeł;
- wszystkie znalezione wartości mają typ źródła dopuszczony przez politykę pola.

Gold jest niezależny od ocenianych artefaktów, lecz został wygenerowany przez
osobny call tego samego modelu. Jest zatem **AI Gold proxy**, a nie ostateczną
prawdą ustaloną przez niezależnego człowieka. Korelacja błędów modelu pozostaje
możliwa.

## 3. Pełne podjęcie zadań pipeline

Naprawiono brakujący zakres w dokładnym lineage kampanii:

| Marka | Przed | Po | Koszt pogłębienia |
|---|---:|---:|---:|
| Bafra Kebab | 4/7 | 7/7 | $0.44522250 |
| Carrefour Express | 6/7 | 7/7 | $0.15382750 |
| Chorten | 6/7 | 7/7 | $0.49938000 |

Bafra i Carrefour wykonały płatny Searcher/Extractor przed błędem lokalnej
walidacji kolejności partycji źródeł. Naprawiono walidator i wznowiono je od
Checkera, bez powtarzania kosztownego wyszukiwania. Chorten zakończył normalny
continuation job. Eksporter benchmarku wybiera teraz najgłębszy workspace w tym
samym `plan_run_id` i dolicza koszty wszystkich continuation jobs, również
płatnych prób zakończonych błędem technicznym.

## 4. Wynik porównania

| Metryka | Direct ChatGPT proxy | Pipeline |
|---|---:|---:|
| Podjęte zadania | 70/70 | 70/70 |
| Propozycje pól | 132 | 47 |
| Zaakceptowane po review | 108 | 41 |
| Akceptacja propozycji | 81.82% | 87.23% |
| Bez korekty / wszystkie propozycje | 50.76% | 42.55% |
| Koszt metody z połową wspólnego review | $2.074482835 | $5.827727825 |
| Zaakceptowane pola / USD | 52.06 | 7.04 |
| Czas researchu łącznie | 7.64 min | 33.76 min |
| Czas AI-assisted review łącznie | 3.55 min | 3.55 min |
| Propozycje porównywalne z `Gold=found` | 112/124 | 44/124 |
| Ścisła zgodność tekstowa w porównywalnych polach | 28.57% | 15.91% |
| Marki spełniające wszystkie pilotażowe bramki | 2/10 | 1/10 |

Koszt całego eksperymentu wyniósł `$4.11550567`:

- Gold: `$1.71185251`, 402 153 tokeny, 20 search calls, 430.99 s;
- direct: `$1.74531251`, 413 267 tokenów, 18 search calls, 458.56 s;
- review: `$0.65834065`, 118 259 tokenów, 0 search calls, 213.75 s.

Koszt Golda nie został przypisany żadnej porównywanej metodzie. Koszt wspólnego
review podzielono po połowie, natomiast pełny czas oczekiwania na review zapisano
dla obu metod.

## 5. Interpretacja

Pipeline ma nieco wyższą precyzję wśród pól, które w ogóle proponuje
(`87.23%` vs `81.82%`), ale jego pokrycie jest zdecydowanie za małe. Zaproponował
47 pól wobec 132 w metodzie direct i tylko 44 ze 124 wartości znalezionych w
Goldzie. To potwierdza wcześniejszą hipotezę o „sterylności”: wieloetapowe
odrzucanie dowodów redukuje ryzyko jednostkowego błędu, ale usuwa zbyt wiele
użytecznych danych L1.

Direct ChatGPT jest około:

- 2,8 raza tańszy per pełna próba;
- 4,4 raza szybszy w samym researchu;
- 7,4 raza wydajniejszy w zaakceptowanych polach na dolara;
- 2,8 raza szerszy pod względem liczby propozycji.

Nie oznacza to, że należy zastąpić cały pipeline jednym promptem. Bezpośrednia
metoda nadal miała 24 odrzucone propozycje i często używała jednego oficjalnego
URL-a do wielu pól. Ten wzorzec jest ekonomiczny dla L1, ale zbyt słaby dla
danych prawnych, inwestycyjnych i L2/L3.

Niska ścisła zgodność tekstowa obu metod pokazuje też, że `gold_exact_rate` jest
zbyt prymitywną metryką dla tekstu, kwot z kwalifikatorami i streszczeń.
Ważniejszy jest wynik semantic review, a w kolejnej wersji potrzebna jest
kanonizacja typu pola (kwota, waluta, VAT, procent, zakres, data) przed
porównaniem.

## 6. Decyzja dla architektury L1

Etap 1 w wariancie operacyjnym AI-assisted jest zakończony i uzasadnia zmianę:

1. L1 powinien zaczynać od jednego taniego, szerokiego przebiegu po oficjalnej
   stronie i jej typowych podstronach.
2. Wyniki należy rozbić deterministycznie na 20 pól i zachować bezpośrednie
   źródła.
3. Pełny Searcher uruchamiać tylko dla braków, niezależnego potwierdzenia i pól
   wysokiego ryzyka.
4. Semantyczny Checker stosować przede wszystkim do liczb, opłat, inwestycji,
   liczby placówek, dat i konfliktów.
5. Dla opisowych pól L1 wystarczy walidacja źródła, świeżości i rynku PL oraz
   szybki review.
6. Status runu zależy od podjęcia 7/7 zadań i pokrycia decyzyjnych pól, nie od
   progu due-diligence 80/100.

Pozostaje jedno ograniczenie metodologiczne: aby nazwać benchmark w pełni
niezależnym eksperymentem człowiek + ChatGPT, trzeba w przyszłości powtórzyć tę
samą próbę z inną osobą, miernikiem aktywnego czasu i bez dostępu do wyników
AI Gold proxy. Brak czasu użytkownika nie został ukryty przez wpisanie
fikcyjnych „czasów Human Review”.

## 7. Artefakty

- `datacollector/benchmarks/pl-l1-gold-v1.json`
- `datacollector/benchmarks/pl-l1-manual-v1.json`
- `datacollector/benchmarks/pl-l1-pipeline-v1.json`
- `datacollector/benchmarks/pl-l1-ai-assisted-experiment-v1.json`
- zaślepiony widok: `/internal/research/benchmark/gold/`
- porównanie: `/internal/research/benchmark/`
