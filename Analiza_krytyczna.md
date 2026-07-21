# Analiza krytyczna projektu portalu i systemu AI research

**Stan na:** 21 lipca 2026 r.  
**Zakres:** datacollector, Research Profiles, pętla agentów, Human Research Workbench, Importer, Publication Gate, Batch Campaigns oraz publiczny katalog franczyz.

## 1. Werdykt wykonawczy

Projekt wyraźnie zbliżył się do celu **od strony infrastruktury**, ale tylko nieznacznie **od strony wartości danych widocznych dla użytkownika**.

Najkrótsza uczciwa ocena brzmi:

- powstał zaawansowany i w dużej części audytowalny prototyp systemu researchowego;
- nie powstała jeszcze konkurencyjna baza danych franczyz;
- bezpośredni koszt API nie jest wysoki;
- koszt złożoności, pracy programistycznej i późniejszego Human Review jest obecnie nieproporcjonalny do liczby użytecznych, opublikowanych pól;
- obecny pipeline jest zbyt rygorystyczny w niewłaściwych miejscach, a jednocześnie za mało skuteczny w pozyskiwaniu najważniejszych danych biznesowych;
- największym bieżącym zagrożeniem nie jest halucynacja modelu, lecz **utrata zaufania przez publikowanie demonstracyjnych liczb i jednoczesne oznaczanie części takich profili jako „Zweryfikowane”**;
- nie należy obecnie uruchamiać masowej kampanii na wszystkie 57 franczyz. Najpierw trzeba skalibrować L1, poprawić semantykę publikacji i wykonać porównawczy benchmark ręczny.

### Ocena etapów dojrzałości

| Obszar | Ocena | Komentarz |
| --- | --- | --- |
| Projekt zakresu i poziomów PL:L1–L3 | dobra baza | Poziomy są jawne, wersjonowane i kumulatywne. |
| Proweniencja i audyt danych | mocna strona | Hashe, niezmienne artefakty, cytaty i lineage są wartościowe. |
| Niezawodność techniczna | prototyp/alpha | Testy przechodzą, ale historia runów ujawniła wiele błędów kontraktów i postprocessingu. |
| Skuteczność pozyskiwania L1 | słaba | Pierwsza kampania dała mało użytecznych pól, a dwie marki nie dały żadnej wartości do normalizacji. |
| Human Review | funkcjonalny prototyp | Jest bezpieczna bramka, lecz skala 61 decyzji na markę jest kosztowna i słabo priorytetyzowana. |
| Publikacja do profilu | bardzo ograniczona | Obecnie tylko jedno pole researchowe zostało faktycznie rzutowane na publiczny model profilu. |
| Jakość publicznego katalogu | niegotowa produkcyjnie | 55 z 57 aktywnych profili to dane demo; część demo jest oznaczona jako zweryfikowana. |
| Gotowość wdrożeniowa researchu | niska | Obraz Docker nie zawiera katalogu `datacollector`, brak osobnej usługi workera i trwałego magazynu artefaktów. |
| Przewaga konkurencyjna | jeszcze niepowstała | Jest potencjalny mechanizm, lecz nie ma jeszcze wyjątkowo dobrego i szerokiego zbioru danych. |

## 2. Jaki jest właściwy cel

Celem nie jest samo zbudowanie pętli agentów. Celem biznesowym jest stworzenie najlepszego polskiego directory franczyz, które jednocześnie:

1. obejmuje możliwie wszystkie aktywne franczyzy;
2. ma więcej użytecznych danych niż konkurencyjne katalogi;
3. łączy dane z wielu źródeł i pokazuje ich pochodzenie;
4. rozróżnia twierdzenia franczyzodawcy, dane rejestrowe, dane niezależne, szacunki i ocenę redakcyjną;
5. umożliwia tanie nasycenie katalogu na poziomie L1 i późniejsze pogłębianie wybranych marek do L2/L3;
6. jest aktualizowalny i potrafi wykryć, które dane się zestarzały;
7. pomaga użytkownikowi podjąć decyzję, a nie tylko prezentuje surowy audyt;
8. pozwala zespołowi redakcyjnemu szybko poprawiać, zatwierdzać i publikować informacje;
9. nie publikuje danych poufnych ani nieudokumentowanych jako pewników;
10. ma mierzalny koszt pozyskania i utrzymania jednego kompletnego profilu.

Obecny projekt zrealizował znaczną część punktów 3, 5, 8 i 9 na poziomie technicznym. Punkty 1, 2, 6, 7 i 10 pozostają w większości niezrealizowane.

## 3. Metoda i ograniczenia tej analizy

Analiza została oparta na:

- aktualnym kodzie repozytorium;
- bazie danych używanej przez portal;
- artefaktach runów Żabki, McDonald's i pierwszej kampanii PL:L1;
- statusach Workbenchów, importów i publikacji;
- zapisanych metrykach użycia API;
- rezultacie testów i kontroli wdrożeniowej Django.

W ramach kontroli uruchomiono:

- 311 testów `datacollector` — wszystkie przeszły;
- 37 testów aplikacji `franchises` i `backoffice` — wszystkie przeszły;
- `manage.py check --deploy` — wykazał 6 ostrzeżeń bezpieczeństwa.

Koszt całej historii został oszacowany przez deduplikację zapisów użycia providera w lokalnych artefaktach, przede wszystkim po `response_id`. To jest **dolne przybliżenie, a nie faktura OpenAI**. Istnieją próby z nieznanym użyciem, a lokalna karta cenowa może różnić się od końcowego rozliczenia konta.

Nie przeprowadzono badań użyteczności z zewnętrznymi użytkownikami. Zapisane w bazie wizyty i leady pochodzą z bardzo krótkiego okresu rozwoju i mogą zawierać dane demonstracyjne, dlatego nie są dowodem product-market fit.

## 4. Co realnie udało się zbudować

### 4.1. Mocne elementy

W projekcie powstały wartościowe fundamenty, których zwykłe ręczne pytanie do chata nie zapewnia:

- rozdzielenie ról Planner → Searcher → Extractor → Checker → Resolver/Executor → Normalizer;
- niezmienne artefakty JSON i SHA-256 całego lineage;
- zachowywanie dowodów, claimów, cytatów i źródeł;
- kontrola, czy URL rzeczywiście pochodził z odpowiedzi narzędzia wyszukiwania;
- zapisywanie kosztu i tokenów na poziomie agentów i prób;
- failure ledger dla płatnych odpowiedzi, które nie przeszły lokalnego postprocessingu;
- Human Review przed importem;
- nieujawnianie publicznie oczekujących propozycji AI;
- idempotentny Importer i Finalizer;
- Publication Gate, który nie rzutuje dowolnego tekstu bezpośrednio na pola modelu;
- poziomy PL:L1, PL:L2 i PL:L3 z rozróżnieniem dostępności publicznej, rejestrowej, ręcznej i prywatnej;
- trwałe zadania, heartbeat, retry błędów przejściowych oraz Batch Campaigns;
- obszerny zestaw testów jednostkowych i integracyjnych.

To jest dobry szkielet systemu redakcyjnego. Gdyby priorytetem był wyłącznie audytowalny data room, kierunek byłby bliski właściwemu.

### 4.2. Czego ten sukces jeszcze nie oznacza

Przejście wszystkich etapów przez skrypt nie oznacza, że profil jest kompletny, wartościowy ani opublikowany. W obecnym UI status kampanii „Zakończona” i licznik „Gotowe” oznaczają tylko, że powstał Workbench. Nie oznaczają:

- przejścia Checkera;
- wykonania pełnego L1;
- wykonania Human Review;
- importu;
- aktualizacji publicznego profilu;
- zdobycia choćby jednego kluczowego parametru inwestycyjnego.

To rozróżnienie jest dziś za słabo widoczne i prowadzi do zawyżonego poczucia postępu.

## 5. Twarde wyniki pierwszej kampanii

Kampania `Pierwsza Testowa Kampania` objęła pięć marek profilem PL:L1. Technicznie wszystkie pięć launchy zakończyło się sukcesem.

| Marka | Koszt znany | Tokeny | Jakość / 80 | Zadania | Pola z propozycją | Finalizacja |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Bafra Kebab | $0.6532 | 148 142 | 27 | 5/13 | 14/61 | brak |
| Bobby Burger | $0.6795 | 133 591 | 9 | 5/13 | 0/61 | brak |
| Carrefour Express | $0.4670 | 101 600 | 8 | 5/13 | 0/61 | brak |
| Chorten | $0.5555 + koszt nieznany | 119 853 | 18 | 5/13 | 7/61 | brak |
| Da Grasso | $0.4264 | 92 530 | 20 | 5/13 | 5/61 | brak |
| **Razem / średnia** | **$2.7816 + 1 nieznana próba** | **595 716** | **16,4 średnio** | **25/65** | **26/305** | **0/5** |

System do budżetu kampanii doliczył rezerwę $0.50 za nieznaną próbę Chortenu. Budżetowy koszt kampanii wyniósł więc $3.2816.

### 5.1. Co te liczby naprawdę znaczą

- Każdy run objął tylko 5 z 13 zadań L1, ponieważ domyślne `initial_task_limit` wynosi 5.
- W każdym Workbenchu 32 z 61 pól pozostały w statusie `not_evaluated`.
- Żaden Checker nie przeszedł progu 80.
- Żaden Workbench nie miał kompletnego scope.
- Wszystkie 305 decyzji pól nadal oczekują na człowieka.
- Żadna z pięciu marek kampanii nie została sfinalizowana ani opublikowana.
- Dwie marki zużyły łącznie około $1.15 i 235 tys. tokenów, a nie dały ani jednego znormalizowanego pola.
- 26 pól z propozycją to tylko 8,5% wszystkich pozycji oczekujących w pięciu Workbenchach.
- „42 normalized values” w podsumowaniach nie oznaczają 42 użytecznych pól — kilka pól zawiera wiele konkurencyjnych wartości. Rzeczywista liczba pól z propozycją wynosi 26.

### 5.2. Claimy i źródła

W kampanii powstało 167 claimów:

- 48 zaakceptowanych przez Checkera — 28,7%;
- 70 wymagających review — 41,9%;
- 49 odrzuconych — 29,3%.

Searcher zapisał 59 źródeł. Checker ocenił 50 z nich, zgodnie z limitem 10 na markę:

- 34/50 zostały pobrane;
- tylko 29/50 zostało poprawnie sparsowanych;
- źródła oficjalne stanowiły 22/59 kandydatów;
- 25/59 miało typ `unknown`;
- Carrefour miał tylko 3 poprawnie pobrane źródła z 10 ocenianych.

Nie jest to wynik katastrofalny dla pierwszego pilota, ale jest zbyt słaby, aby rozpoczynać masową produkcję danych.

## 6. Koszt: czy system był zbyt drogi

### 6.1. Koszt API

Wszystkie lokalne artefakty zawierają łącznie co najmniej:

- 261 unikalnych zapisanych wywołań providera;
- 3 707 075 tokenów;
- $16.0836 znanego kosztu, w tym $1.37 kosztu narzędzi web search;
- co najmniej 5 odrębnych prób o nieznanym użyciu.

Największe składniki znanego kosztu:

| Agent | Wywołania | Tokeny | Koszt znany |
| --- | ---: | ---: | ---: |
| Extractor | 185 | 1 432 748 | $5.9440 |
| Searcher | 24 | 1 113 361 | $4.9625 |
| Checker | 26 | 944 586 | $3.9381 |
| Planner + Resolver + Normalizer | 26 | 216 380 | $1.2392 |

Sam koszt API **nie jest nadmierny**. Nawet kilkadziesiąt dolarów za wstępne nasycenie katalogu byłoby akceptowalne, gdyby wyniki rzeczywiście stawały się użytecznymi i publikowanymi profilami.

Problemem jest koszt jednostki wartości:

- kampania kosztowała około $0.56 znanego kosztu na markę;
- ale dała średnio tylko 5,2 pola z propozycją na markę;
- nie dała ani jednego pola zaakceptowanego przez człowieka i opublikowanego;
- po kampanii pozostaje 305 decyzji redakcyjnych.

Obecnie mierzymy tokeny, lecz nie mierzymy najważniejszego: **zaakceptowanych, publicznie użytecznych pól na dolara i na minutę pracy researchera**.

### 6.2. Koszt inżynieryjny

To on jest obecnie zbyt wysoki względem potwierdzonego efektu.

W analizowanym obszarze znajduje się około 35,9 tys. linii kodu produkcyjnego. Sam `datacollector/schemas.py` ma 5324 linie, a `datacollector/cli.py` 4204 linie. W ciągu kilku dni powstało 38 commitów związanych z intensywną przebudową, wiele o nieprecyzyjnych nazwach typu `mass`, `adjust collector loop` lub `Step 14,5`.

Nie można z repozytorium wyliczyć wartości czasu pracy, ale z całą pewnością dominuje on nad kosztem $16 API. W tej fazie projekt zoptymalizował kontrolę i audyt wcześniej niż udowodnił, że potrafi tanio tworzyć atrakcyjny profil L1.

### 6.3. Koszt Human Review

Każdy Workbench L1 ma 61 pól. Pięć runów dało 305 oczekujących decyzji. Nawet przy hipotetycznych 30–60 sekundach na pole daje to 2,5–5 godzin pracy dla tej kampanii. Dla 57 franczyz byłoby to około 29–58 godzin, zanim uwzględnimy czytanie źródeł i poprawianie wartości.

To nie jest jeszcze automatyzacja masowego katalogu. To automatyczne przygotowywanie obszernej kolejki ręcznej.

## 7. Czy łatwiej byłoby szukać ręcznie z ChatGPT

### Odpowiedź: obecnie tak — dla małej liczby marek i podstawowego L1

Dla pięciu marek badacz korzystający z przeglądarki, jednolitego formularza i jednego dobrze przygotowanego promptu prawdopodobnie szybciej uzyskałby:

- oficjalną stronę;
- stronę franczyzową;
- kontakt;
- deklarowaną inwestycję;
- opłaty;
- wymagania lokalu i kandydata;
- liczbę placówek;
- krótkie podsumowanie z linkami.

Nie uzyskałby jednak automatycznie:

- niezmiennego lineage;
- walidacji identyfikatorów;
- powtarzalnego schematu dla setek marek;
- claimów połączonych z cytatami;
- trwałego rejestru kosztów i błędów;
- kontrolowanej publikacji i aktualizacji;
- możliwości różnicowego rerunu.

Dlatego właściwym rozwiązaniem nie jest powrót do całkowicie ręcznej pracy, lecz **model hybrydowy**:

1. system podpowiada źródła i wstępnie wypełnia najważniejsze pola;
2. researcher widzi tylko pola wartościowe albo niepewne, nie wszystkie 61;
3. proste fakty z oficjalnej domeny przechodzą tanią walidację deterministyczną;
4. LLM jest używany do ekstrakcji i rozstrzygania niejednoznaczności, nie do każdego etapu;
5. pełny Checker LLM uruchamia się tylko dla finansów, umów, sprzeczności i ryzyk;
6. L2/L3 pozostają procesem bardziej audytowym i ręcznym.

### Konieczny benchmark

Należy zbadać 10 marek dwiema metodami:

- A: researcher + ChatGPT + formularz;
- B: obecny pipeline + Human Review.

Dla obu metod trzeba zmierzyć ten sam zestaw 15–20 pól, czas, koszt, liczbę błędów, liczbę zaakceptowanych wartości i liczbę wartości nadal aktualnych po niezależnym audycie. Bez takiego eksperymentu dalsza rozbudowa agentów jest optymalizacją bez wiarygodnego punktu odniesienia.

## 8. Czy dane są zbyt „sterylne”

Tak, ale problem jest bardziej złożony. Dane są jednocześnie:

- **nadmiernie sterylne** — użyteczne informacje są odrzucane z powodów formalnych;
- **semantycznie niedoczyszczone** — zachowane wartości bywają marketingowymi fragmentami tekstu lub konkurencyjnymi wariantami;
- **mało decyzyjne** — najłatwiej przechodzą nazwa, kategoria, opis i kontakt, a nie ekonomika inwestycji.

### 8.1. Najważniejszy błąd kalibracji: recency na poziomie całego zadania

W Bobby Burger wszystkie 60 claimów dostało `stale_or_undated`. W Carrefour Express dotyczyło to 18 z 22 claimów. W efekcie obie marki zakończyły się zerem znormalizowanych wartości.

To pokazuje, że okres ważności jest stosowany zbyt szeroko. Nazwa marki, rok założenia lub opis kategorii nie muszą mieć publikacji z ostatnich 365 dni. Statyczna oficjalna strona często nie ma daty, lecz może być aktualna w dniu pobrania. Inaczej należy oceniać:

- nazwę i oficjalną domenę — aktualność przez działającą stronę i spójność marki;
- historię marki — informacja trwała, bez rocznego wygaśnięcia;
- opłaty i inwestycję — krótki termin ważności, np. 6–12 miesięcy;
- liczbę placówek — wymagany jawny okres odniesienia;
- KRS — bieżący odpis lub data pobrania;
- wyniki finansowe — okres sprawozdawczy i metodologia.

Recency musi być polityką pola, nie całego zadania.

### 8.2. „Zweryfikowane” nie zawsze znaczy użyteczne

Przykładowe propozycje Bafra Kebab zawierają:

- kilka różnych opisów kategorii;
- trzy marketingowe wersje publicznego podsumowania;
- trzy różne zapisy wymaganego kapitału;
- twierdzenia typu „największa i najszybciej rozwijająca się sieć”.

System poprawnie oznacza część z nich jako `multiple_values` lub wymagające potwierdzenia, ale nie tworzy jeszcze dobrego redakcyjnego wniosku. Chorten zwrócił głównie nazwę, kontakt, ogólny model i historię. Da Grasso zwróciło pięć pól, z których część to opis marketingowy.

Zbyt rygorystyczny Checker nie rozwiązał więc problemu jakości. Zwiększył precision kosztem recall, ale pozostawił pracownikowi semantyczne porządkowanie tekstu.

### 8.3. Potrzebna jest typologia informacji

Każda publiczna wartość powinna mieć jeden z jawnych typów:

- fakt z rejestru;
- fakt ze źródła oficjalnego;
- deklaracja franczyzodawcy;
- informacja z niezależnego źródła;
- szacunek redakcyjny;
- obliczenie systemowe;
- opinia lub sygnał jakościowy;
- informacja z dokumentu prywatnego;
- brak danych po sprawdzeniu.

Nie należy wymagać dwóch niezależnych źródeł dla nazwy lub oficjalnego e-maila. Należy ich wymagać dla stwierdzeń o rentowności, pozycji rynkowej albo typowym wyniku placówki. Publiczny portal może pokazywać wartości użyteczne, ale oznaczone jako „deklaracja sieci” i datowane, zamiast całkowicie je ukrywać.

## 9. Przypadek Żabki: kosztowna pętla i problem gałęzi lineage

Żabka najlepiej pokazuje zarówno siłę, jak i słabość projektu:

- katalog historyczny miał 37 zadań i 258 pól;
- po wielu iteracjach powstał pełny Checker `check-r039.json`, który objął 37/37 zadań;
- jego wynik wyniósł tylko 17/80, z 99 krytycznymi brakami;
- katalog runu Żabki zajmuje około 191 MB;
- opublikowany import pochodzi jednak ze znacznie wcześniejszego `normalized-r014.json`, opartego na `check-r012.json` — tylko 5/37 zadań, jakość 22/80;
- późniejszy `check-r041.json` ponownie rozgałęzia się od `check-r012.json`, ma 32 nieocenione zadania i nie reprezentuje pełnej gałęzi `r039`.

Niezmienność artefaktów jest dobra, ale nie ma wystarczająco czytelnego pojęcia **kanonicznej głowy runu**. Można kontynuować stary Workbench i wyprodukować nowszy numer iteracji na starszej gałęzi. Dla pracownika wygląda to jak „najnowszy wynik”, choć ma mniejszy zakres.

To jest ważny błąd workflow. Potrzebny jest jawny graf lineage oraz:

- oznaczenie gałęzi `current`, `superseded`, `forked`, `published`;
- blokada przypadkowego kontynuowania starej gałęzi;
- porównanie zakresu przed startem;
- ostrzeżenie, że nowy run nie dziedziczy nowszych wyników;
- możliwość świadomego merge albo rozpoczęcia nowego wydania od kanonicznego Checkera.

## 10. Krytyczna analiza kodu i architektury

### 10.1. Co jest dobre

Kod ma więcej zabezpieczeń niż typowy szybki prototyp AI:

- Pydantic rygorystycznie sprawdza kontrakty;
- zapisy są atomowe i nie nadpisują artefaktów;
- pobrane dokumenty mają hash i kontrolę ścieżki;
- dane oczekujące nie trafiają publicznie;
- publikacja jest transakcyjna i zachowuje poprzednią wartość;
- retry jest ograniczone, a nieznany koszt dostaje rezerwę;
- testy obejmują znaczną część wcześniej znalezionych regresji;
- załączniki Workbencha są poza publicznym `MEDIA_ROOT` i mają limit wielkości.

To należy zachować.

### 10.2. Monolity i nadmierna odpowiedzialność

Największe pliki:

- `datacollector/schemas.py` — 5324 linie;
- `datacollector/cli.py` — 4204 linie;
- `datacollector/agents/checker.py` — 2263 linie;
- `datacollector/agents/searcher.py` — 1863 linie;
- `datacollector/agents/extractor.py` — 1719 linii;
- `datacollector/agents/executor.py` — 1674 linie;
- `src/saashome/franchises/models.py` — 1556 linii.

Przykładowe rozmiary funkcji:

- `validate_search_results` — około 763 linii;
- `validate_checker_results` — około 681 linii;
- `create_check_results` — około 634 linii;
- `_run_loop` — około 576 linii;
- `validate_extraction_results` — około 502 linie;
- `_build_task_results` — około 490 linii.

To utrudnia lokalne rozumowanie, review, testowanie wariantów i zmianę schematu. Walidator nie powinien jednocześnie być specyfikacją domeny, migracją zgodności, mechanizmem naprawy oraz kontrolą całego grafu lineage.

Rekomendowane rozdzielenie:

- kontrakty wejścia/wyjścia per agent;
- osobne polityki domenowe;
- osobne walidatory provenance;
- migratory/adaptery starszych wersji;
- małe serwisy merge/reconcile;
- CLI jako cienka warstwa wywołująca use case.

### 10.3. Walidacja zbyt późno — po płatnym wywołaniu

W artefaktach znajduje się 16 failure ledgerów:

- 12 dla Extractora;
- 4 dla Searchera;
- 15 błędów `postprocessing_error`;
- 1 `provider_exception`.

Historia prac zawierała m.in. niespójne usage metadata, query overrides, liczbę tool calli, merge wyników, limit claimów i zbyt długie `related_claim_ids`.

To pokazuje, że wiele inwariantów ujawniało się dopiero po kosztownym wywołaniu. Ścisły validator jest zaletą, ale kontrakt odpowiedzi musi być projektowany tak, aby:

- model nie musiał reprodukować danych deterministycznych;
- wszystkie identyfikatory i limity były wstrzykiwane lokalnie;
- odpowiedź modelu zawierała tylko najmniejszy zakres semantycznej decyzji;
- postprocessing potrafił naprawić bezpieczne odchylenia bez utraty całego wyniku;
- walidacja requestu i przewidywalność struktury były sprawdzane przed wywołaniem.

### 10.4. Zbyt szerokie `except Exception`

W badanym kodzie produkcyjnym występuje około 40 szerokich przechwyceń `except Exception`. Część jest uzasadniona na granicy procesu lub providera, ale w obecnej liczbie może:

- zamieniać błąd programistyczny w zwykły `provider_exception`;
- utrudniać rozróżnienie błędu danych, infrastruktury i kodu;
- prowadzić do fallbacku deterministycznego, który wygląda jak poprawny wynik;
- maskować regresję, dopóki nie zostanie ręcznie zauważona w warningach.

Należy wprowadzić hierarchię wyjątków i fail-fast dla błędów programistycznych. Fallback powinien być jawnie oznaczany jako zdegradowany wynik, a nie tylko jako `strategy_source` w głębi JSON.

### 10.5. Bespoke queue i subprocessy

Worker jest management commandem, który odpytuje PostgreSQL i uruchamia `python -m datacollector` przez `subprocess.Popen`. To działa lokalnie, ale ma ograniczenia:

- brak osobnego systemu kolejkowego, retry policy, dead-letter queue i dashboardu operacyjnego;
- log jest obcinany do 100 tys. znaków;
- status etapu bywa wywnioskowany z pojawienia się plików;
- parser stdout szuka pierwszego obiektu JSON, co jest kruche;
- workery launchy mają pierwszeństwo przed zwykłymi jobami Workbencha, więc długa kampania może opóźniać finalizacje;
- idempotencja lokalnego etapu nie gwarantuje idempotencji płatnego requestu providera;
- obsługa restartu zależy od heartbeat i lokalnych plików.

Na pilota nie trzeba od razu wprowadzać Celery. Trzeba jednak oddzielić interfejs kolejki od implementacji, zapisywać maszynowy wynik poza stdout i zaplanować przejście do trwałego workera z obserwowalnością.

### 10.6. Research nie jest obecnie wdrażalny z istniejącego Dockerfile

`Dockerfile` kopiuje do obrazu tylko `src/saashome`, a nie katalog `datacollector`. Nie definiuje również osobnego procesu `process_research_jobs`. W efekcie aktualna funkcja researchu jest praktycznie lokalna i nie ma kompletnej ścieżki produkcyjnego uruchomienia.

Dodatkowo:

- artefakty zapisują absolutne ścieżki z lokalnego komputera;
- runy i prywatne dokumenty opierają się na lokalnym filesystemie;
- nie ma konfiguracji współdzielonego object storage;
- nie ma polityki backupu i retencji;
- prywatne pliki są sprawdzane po rozszerzeniu i wielkości, ale nie ma skanowania malware ani weryfikacji rzeczywistego typu MIME.

W środowisku z efemerycznym filesystemem utrata kontenera może odciąć Workbench od jego lineage. Przy wielu workerach pliki nie muszą być dostępne na każdym hoście.

Lokalny katalog `datacollector/data/runs` zajmuje już około 264 MB dla siedmiu marek i eksperymentów, z czego sama Żabka około 191 MB. Te same duże PDF-y oraz kolejne, kompletne wersje scalonych ekstrakcji występują wielokrotnie. Niezmienność nie powinna oznaczać fizycznego kopiowania każdego dokumentu do każdej iteracji. Potrzebny jest content-addressable storage: jeden blob per SHA-256 i małe manifesty wskazujące, które wydanie go używa.

### 10.7. Konfiguracja bezpieczeństwa nie jest gotowa produkcyjnie

`manage.py check --deploy` zgłosił:

- brak HSTS;
- brak wymuszenia HTTPS;
- niebezpieczną lub zbyt słabą wartość `SECRET_KEY` w aktualnym środowisku;
- brak `SESSION_COOKIE_SECURE`;
- brak `CSRF_COOKIE_SECURE`;
- `DEBUG=True`.

Część ostrzeżeń może wynikać z lokalnego środowiska developerskiego, ale `DEBUG` jest obecnie ustawione na stałe w kodzie, a nie przez zmienną środowiskową. Nie należy wdrażać publicznego portalu z taką konfiguracją. Potrzebne są osobne ustawienia development/production oraz automatyczny `check --deploy` w CI.

### 10.8. Koszt i budżet nie są jeszcze finansowym źródłem prawdy

Karta cenowa jest zapisana w kodzie i daje dobrą estymację, ale:

- nie jest porównywana z eksportem/fakturą providera;
- timeout może zostawić koszt nieznany;
- rezerwa $0.50 jest arbitralnym zabezpieczeniem, nie rzeczywistym rozliczeniem;
- `max_total_cost_usd` kampanii działa głównie jako kontrola rezerwacji, nie jako ścisła globalna blokada faktycznego spendu;
- rozpoczęty etap może przekroczyć limit, co UI wprawdzie sygnalizuje;
- brak kosztu na zaakceptowane pole, a jest tylko koszt na run.

### 10.9. Brak jawnego lifecycle danych

System zapisuje daty źródeł i publikacji w claimach, lecz kompaktowy profil nadal ma głównie jedną datę `Franchise.updated_at`. Zmiana dowolnego pola może wyglądać jak aktualizacja całego profilu. Jedno `data_source_url` nie reprezentuje wieloźródłowego profilu.

Potrzebne są per pole:

- `observed_at`;
- `valid_as_of`;
- `source_type`;
- `confidence`;
- `next_review_at`;
- status `current/stale/disputed/superseded`;
- źródło i decyzja redakcyjna.

### 10.10. Testy są mocne, ale nie wystarczają

348 uruchomionych testów przeszło. To istotna zaleta. Nie ma jednak widocznej konfiguracji CI, lintingu, type checkingu, skanowania zależności ani mierzonego coverage.

Testy w dużej części potwierdzają zachowanie, które zostało zakodowane po kolejnych awariach. Brakuje najważniejszego rodzaju testu dla produktu AI: wersjonowanego zestawu ewaluacyjnego z ręcznie ustaloną prawdą dla realnych polskich marek.

Powinien istnieć „gold set” co najmniej 10 franczyz i 15–20 pól L1, z którym każda zmiana promptu, modelu, Searchera lub Checkera jest porównywana pod względem precision, recall, kosztu i czasu.

### 10.11. Dokumentacja i historia zmian

`datacollector/README.md` jest bardzo obszerny i dokładny, ale staje się równoległą specyfikacją całego systemu. Root `README.md` przypomina narastający dziennik kolejnych MVP. Komentarz w settings nadal mówi o projekcie wygenerowanym przez Django 5.0.14, gdy wymaganie to Django 6.0.

Vague commit messages utrudniają ustalenie, dlaczego zmienił się kontrakt lub scoring. Dla systemu audytowego potrzebne są:

- ADR-y dla kluczowych decyzji;
- changelog schematów i promptów;
- precyzyjne commity;
- jedna aktualna dokumentacja operacyjna;
- automatyczne sprawdzanie zgodności dokumentacji profili z kodem.

## 11. Krytyczna analiza obecnego portalu

### 11.1. Najpoważniejszy problem: publiczne dane demonstracyjne

W bazie jest 57 aktywnych franczyz:

- 55 ma `data_status=demo`;
- tylko 2 mają status researchowy;
- 10 profili demo ma jednocześnie `is_verified=True`;
- wszystkie 10 profili oznaczonych w bazie jako zweryfikowane to profile demo;
- istnieje tylko 1 aktualne rzutowanie pola researchowego na model publiczny: nazwa McDonald's.

Jednocześnie publiczny katalog jest pozornie bardzo kompletny:

- 56/57 profili ma minimalną inwestycję;
- 53/57 ma opłatę wstępną;
- 49/57 ma szacowany payback;
- 49/57 ma przychód i zysk dojrzałej placówki;
- tylko 1/57 ma `data_source_url`.

To nie jest przewaga danych. To wypełnienie interfejsu demonstracyjnymi liczbami.

Na stronie szczegółowej pojawia się ostrzeżenie o danych demo, ale karta na liście pokazuje inwestycję i opłatę bez takiego oznaczenia. Filtry i porównywarka również używają tych wartości. Co gorsza, profil może jednocześnie pokazać:

- badge „Zweryfikowane”;
- komunikat „Dane zostały potwierdzone przez właściciela marki lub redakcję”;
- niżej ostrzeżenie, że liczby są demonstracyjne.

To jest sprzeczność i ryzyko reputacyjne. Dane finansowe demo powinny zostać usunięte z publicznych filtrów i kart, a najlepiej wyzerowane, nie tylko opatrzone ostrzeżeniem.

### 11.2. „Zweryfikowane” miesza różne pojęcia

`display_verified` jest prawdziwe, jeśli:

- `Franchise.is_verified` jest prawdziwe; **lub**
- aktywna promocja ma typ `verified_badge`.

Publiczny badge sugeruje, że dane są sprawdzone. Tymczasem może oznaczać stan seedu, przejęcie profilu, decyzję redakcyjną albo płatną promocję. Tych pojęć nie wolno łączyć.

Potrzebne są osobne statusy:

- właściciel profilu potwierdzony;
- dane przekazane przez markę;
- pole potwierdzone źródłem;
- profil sprawdzony przez redakcję;
- płatna promocja;
- research częściowy/pełny;
- data ostatniej kontroli.

Płatny produkt nie może nadawać badge'a wyglądającego jak niezależna weryfikacja danych.

### 11.3. Profil publiczny jest bogaty wizualnie, lecz ma słabą hierarchię zaufania

Pozytywne elementy:

- czytelny snapshot inwestora;
- status danych i ostrzeżenie demo na szczególe;
- sekcje ekonomiki, sieci, umowy, lokalizacji i źródeł;
- CTA, shortlisty, porównanie i raport researchu;
- oddzielenie promowania od zwykłego profilu.

Problemy:

- najważniejsze liczby nie mają źródła i daty bezpośrednio obok wartości;
- `updated_at` profilu nie oznacza daty weryfikacji każdego pola;
- nie ma widocznego rozróżnienia „twierdzenie marki” kontra „potwierdzenie niezależne”;
- karta listy nie pokazuje `data_status`;
- mapa zajmuje dużą część desktopowego widoku, choć lokalizacje są demonstracyjne i mogą być mniej ważne od porównania ekonomiki;
- pierwsza warstwa profilu nie pokazuje syntetycznych ryzyk, braków i pytań do franczyzodawcy;
- nie ma prostego „dlaczego ta franczyza może / nie może pasować do mnie”;
- wartości liczbowe dominują nawet wtedy, gdy są demonstracyjne.

### 11.4. Pełny raport researchu jest bardziej audytem niż produktem konsumenckim

Raport dobrze pokazuje provenance, ale dla typowego poszukiwacza franczyzy jest zbyt techniczny:

- eksponuje `Plan run`, `Normalization`, `Review`, SHA i identyfikatory techniczne;
- używa pojęć `claim`, `checker`, `pipeline_status`;
- pokazuje długą listę braków i pól;
- brakuje redakcyjnego podsumowania decyzji, najważniejszych liczb, czerwonych flag i porównania z rynkiem.

Powinny istnieć trzy warstwy:

1. **Profil decyzyjny** — najważniejsze dane, ryzyka, koszty, fit i aktualność;
2. **Źródła i metodologia** — dowody przy polach, bez nadmiaru technicznych ID;
3. **Audyt techniczny** — pełne claimy, cytaty, hashe i lineage dla zaawansowanych użytkowników lub redakcji.

### 11.5. Workbench jest funkcjonalny, ale skaluje wysiłek, nie decyzję

One-click accept/reject/gap jest dobrym krokiem. Nadal jednak researcher dostaje 61, 179 albo 273 pola. Brakuje:

- kolejki „najpierw pola widoczne publicznie”;
- sortowania według wartości biznesowej i niepewności;
- grupowego zatwierdzania prostych faktów z jednej oficjalnej strony;
- widoku tylko nowych/zmienionych wartości;
- diffu z profilem publicznym;
- ostrzeżenia przed kontynuacją starej gałęzi;
- wyceny pozostałego Human Review;
- wskaźnika „ile publicznych pól przybędzie po finalizacji”;
- jasnego komunikatu, że zakończony launch nie oznacza publikacji.

### 11.6. Nie ma jeszcze dowodu użyteczności

Baza zawiera 462 wizyty, 215 eventów, 28 leadów i zero aktualnych zapisanych franczyz, ale dane pochodzą z krótkiego okresu developmentu i obejmują seedy/testy. Nie należy z nich wnioskować o konwersji.

Przed kolejną dużą rozbudową potrzebne są zadania testowe z 5–10 realnymi użytkownikami:

- znajdź franczyzę do budżetu X;
- porównaj trzy marki;
- określ, której liczbie ufasz i dlaczego;
- wskaż trzy pytania do franczyzodawcy;
- zapisz ofertę lub wyślij lead.

## 12. Jak powinien wyglądać końcowy portal

### 12.1. Katalog

Każda karta powinna pokazywać tylko dane rzeczywiste albo jawne `brak danych`:

- nazwa, kategoria i oficjalna strona;
- inwestycja jako zakres z datą i typem źródła;
- opłata wstępna;
- liczba placówek w Polsce;
- poziom kompletności L1/L2/L3;
- świeżość;
- oznaczenie „deklaracja marki”, „rejestr”, „redakcja”, nie ogólne „verified”;
- przycisk porównania i zapisania.

Demo powinno istnieć tylko w środowisku developerskim lub być wyraźnie odseparowane od katalogu użytkownika.

### 12.2. Profil marki

Pierwszy ekran:

- przedział inwestycji;
- opłaty;
- wymagany kapitał;
- placówki i trend;
- format lokalu;
- data danych;
- kompletność i confidence;
- główne ryzyka;
- CTA i porównanie.

Dalsze sekcje:

- inwestycja i opłaty;
- ekonomika z wyjaśnieniem, czy to deklaracja, szacunek czy dane z próby;
- wymagania kandydata;
- wsparcie i szkolenia;
- umowa i terytorium;
- sieć i jej ruch;
- doświadczenia franczyzobiorców i sygnały jakościowe;
- ryzyka, spory, pytania otwarte;
- źródła oraz historia zmian.

### 12.3. Warstwa premium/L3

- dokumenty prywatne wyłącznie dla uprawnionych;
- redagowane podsumowania umów;
- scenariusze ekonomiczne;
- dane jednostkowe i porównanie benchmarków;
- jawne założenia i zakres odpowiedzialności;
- log dostępu, retencja i zgody na dokumenty.

### 12.4. Backoffice

- dashboard pokrycia całego katalogu;
- lista marek według brakujących pól o największej wartości;
- plan kampanii wyliczony z braków, nie z pełnego profilu;
- kolejka zmian zamiast ponownego review wszystkich pól;
- canonical lineage head;
- koszt na zatwierdzone pole i przewidywany czas review;
- alerty starych danych;
- bulk actions tylko dla jednorodnych, niskiego ryzyka faktów;
- eksport audytu i rollback publikacji.

## 13. Najważniejsze błędy kierunku popełnione podczas prac

1. **Zbudowano dużą maszynę przed zmierzeniem prostego procesu ręcznego.** Nie ustalono kosztu i jakości baseline'u researcher + ChatGPT.
2. **Za wcześnie przeniesiono standard FDD na szeroki polski katalog.** Profile PL poprawiły sytuację, ale L1 nadal ma 61 pól i część ciężaru due diligence.
3. **Domyślny launch nie realizuje deklarowanego poziomu.** PL:L1 ma 13 zadań, lecz kampania domyślnie wykonuje tylko 5.
4. **Optymalizowano integralność artefaktów przed skutecznością danych.** System świetnie potrafi udowodnić, dlaczego ma mało wartości.
5. **Scoring był traktowany jako cel.** Kolejne pętle Żabki zwiększały zakres, ale nie prowadziły stabilnie do jakości 80 ani publikacji.
6. **Zastosowano zbyt ogólne reguły freshness.** To wyzerowało wyniki Bobby Burger i Carrefour Express.
7. **Nie wykorzystano istniejących oficjalnych URL-i jako kontrolowanych seedów.** Kampania przekazuje pusty `known_official_website`, choć 54/57 profili ma website URL. System płaci za ponowne znalezienie znanej strony.
8. **„Sukces techniczny” został pokazany jako „Gotowe”.** Powstanie Workbencha jest dopiero początkiem pracy redakcyjnej.
9. **Pozostawiono publiczne liczby demo jako substytut pokrycia.** To ukrywa realną lukę danych i może podważyć zaufanie.
10. **Połączono weryfikację danych, właściciela i promocję.** Jeden badge ma zbyt wiele znaczeń.
11. **Brak kanonicznej gałęzi lineage.** Nowszy numer iteracji może reprezentować starszy, węższy zakres.
12. **Nie zaprojektowano od początku produkcyjnego storage i workera.** Lokalny filesystem i ręczny worker nie skalują się na deploy.

## 14. Co należy zachować, uprościć i zatrzymać

### Zachować

- niezmienne artefakty i hashe;
- rozdzielenie źródło → passage → claim → wartość;
- Human Review i Publication Gate;
- wersjonowane profile;
- zachowanie usage po błędzie;
- bezpieczne, jawne fallbacki;
- możliwość dodawania prywatnych dokumentów;
- testy regresji kontraktów.

### Uprościć

- Planner dla PL:L1 powinien być deterministyczny; płatne planowanie nie daje obecnie proporcjonalnej wartości;
- Checker LLM powinien oceniać tylko ryzykowne lub niejednoznaczne claimy;
- Normalizer powinien działać tylko na polach wymagających semantycznej konwersji;
- L1 powinien mieć 15–25 naprawdę publicznych i decyzyjnych pól, nie 61 pozycji do ręcznego kliknięcia;
- CLI powinno zostać rozbite na use case'y i cienkie komendy;
- modele schematów należy podzielić per etap i wersję;
- Workbench powinien pokazywać domyślnie pola z wartością lub zmianą.

### Na razie zatrzymać

- masowe uruchomienie obecnego L1 dla wszystkich marek;
- dalsze dokładanie agentów;
- mechaniczne pętle do progu 80;
- rozwijanie L2/L3 przed ustabilizowaniem ekonomiki L1;
- publikowanie lub filtrowanie po liczbach demo;
- traktowanie większej liczby pól jako automatycznie lepszego produktu.

## 15. Zalecany plan naprawczy

### Etap 0 — ochrona wiarygodności, przed kolejną kampanią

Priorytet P0:

1. Usunąć publiczne wartości finansowe demo albo ukryć je we wszystkich kartach, filtrach, rankingach, JSON-LD i porównaniach.
2. Ustawić `is_verified=False` dla wszystkich profili demo.
3. Rozdzielić badge właściciela, danych, researchu i promocji.
4. Usunąć możliwość, by płatna promocja wyglądała jak niezależna weryfikacja.
5. Zmienić status launchu z „Gotowe” na „Draft do Human Review — zakres 5/13”.
6. Pokazywać przy kampanii liczbę pól, które realnie mogą trafić na profil.
7. Włączyć `DEBUG` przez environment i naprawić sześć ostrzeżeń `check --deploy` przed produkcją.

### Etap 1 — benchmark i redefinicja L1

1. Wybrać 10 marek z różnych kategorii i dostępności źródeł.
2. Zdefiniować 15–20 pól, które użytkownik rzeczywiście wykorzystuje w decyzji.
3. Przygotować ręczny gold set z linkami i datami.
4. Porównać researcher + ChatGPT z pipeline'em.
5. Ustalić field-specific freshness i source policy.
6. Rozdzielić „wartość znaleziona” od „wartość zdatna do publikacji”.

Proponowane bramki jakości L1:

- wszystkie zadania L1 zostały co najmniej podjęte;
- minimum 8–12 decyzyjnych pól na markę ma propozycję;
- co najmniej 60% propozycji przechodzi Human Review bez edycji;
- review trwa nie więcej niż 10–15 minut na markę;
- co najmniej 10 zaakceptowanych pól na $1 znanego kosztu;
- zero nieoznaczonych wartości demonstracyjnych;
- 100% publicznych liczb ma typ źródła i datę ważności/pobrania.

Są to proponowane cele pilotażowe, które należy zweryfikować empirycznie, nie kolejne twarde dogmaty.

### Etap 2 — tani, skuteczny L1

1. Używać istniejącego `website_url` jako `unverified_seed`, a następnie go walidować.
2. Uruchamiać deterministyczny Planner dla stałego profilu.
3. Najpierw pobierać oficjalną stronę i typowe podstrony bez Searchera.
4. Searchera używać dla braków, katalogów, rejestrów i niezależnego potwierdzenia.
5. Grupować ekstrakcję dokumentów, ograniczać pełne przesyłanie powtarzalnych kontekstów.
6. Cache'ować pobrania po canonical URL i hash treści między markami/runami.
7. Uruchamiać semantyczny Checker tylko dla pól wysokiego ryzyka.
8. Automatycznie kończyć cały L1 albo nazywać wynik `partial`, nie `succeeded`.

### Etap 3 — uporządkowanie architektury

1. Wydzielić domenę, orchestration, storage i adapter OpenAI.
2. Zmniejszyć ogromne funkcje i walidatory.
3. Wprowadzić wersjonowane adaptery schema migration zamiast rozgałęzień w głównych validatorach.
4. Dodać canonical lineage head oraz bezpieczne fork/merge.
5. Przenieść artefakty i prywatne pliki do szyfrowanego object storage z retencją i backupem.
6. Wprowadzić prawdziwy proces workera w deployu, structured logs, metryki i alerty.
7. Dodać malware scan i kontrolę rzeczywistego typu plików.
8. Dodać CI: testy, lint, type check, migration check, security check i dependency audit.

### Etap 4 — produkcja danych

1. Uruchomić poprawione L1 na 20–30 markach.
2. Zmierzyć acceptance rate i czas Human Review.
3. Opublikować wyłącznie sprawdzone wartości.
4. Zbudować dashboard pokrycia najważniejszych pól.
5. Dopiero po osiągnięciu bramek rozszerzyć kampanię na cały katalog.
6. L2 uruchamiać dla marek popularnych, dobrze rokujących lub mających wysoką intencję użytkowników.
7. L3 prowadzić tylko dla wybranych marek, klientów premium i dokumentów przekazanych legalnie.

### Etap 5 — przewaga produktu

1. Porównania oparte na rzeczywistych i datowanych danych.
2. Redakcyjne podsumowanie korzyści, ryzyk i pytań do sieci.
3. Scenariusze ekonomiczne zamiast jednej liczby payback.
4. Alerty zmian opłat, liczby placówek i warunków.
5. Mechanizm zgłaszania korekty przez markę i użytkownika.
6. Historia wartości i źródeł.
7. Benchmark kategorii oraz „dlaczego ta oferta pasuje do mojego profilu”.

## 16. Kluczowe KPI, których dziś brakuje

Pipeline powinien być zarządzany przez następujące mierniki:

- zaakceptowane publiczne pola / USD;
- zaakceptowane publiczne pola / minuta Human Review;
- odsetek propozycji zaakceptowanych bez edycji;
- odsetek pól poprawionych i odrzuconych;
- kompletność 15–20 pól rdzeniowych, nie wszystkich pól katalogu;
- odsetek wartości z aktualnym oficjalnym źródłem;
- odsetek twierdzeń wydajnościowych z niezależnym potwierdzeniem;
- średni wiek każdej klasy danych;
- P50/P95 czasu i kosztu runu;
- liczba runów zakończonych Workbenchem, finalizacją i publikacją — osobno;
- liczba profili z co najmniej jednym źródłem per wartość;
- liczba stale/forked workspaces;
- wzrost konwersji profilu po dodaniu zweryfikowanych danych;
- wykorzystanie porównywarki, zapisów i leadów na profilach o różnej kompletności.

## 17. Ostateczna rekomendacja

Nie należy wyrzucać obecnego systemu. Zawiera wartościowy mechanizm audytu, który trudno byłoby odtworzyć prostym chatem. Nie należy jednak rozwijać go dalej w obecnym kierunku „więcej agentów, więcej pól, więcej pętli”.

Najlepsza decyzja to:

1. potraktować obecny kod jako **audytowalny silnik backoffice w fazie alpha**;
2. natychmiast zabezpieczyć wiarygodność publicznego katalogu;
3. uprościć i skalibrować L1 na podstawie ręcznego benchmarku;
4. mierzyć wynik biznesowy, nie tokeny i sam quality score;
5. dopiero później skalować kampanie i pogłębiać L2/L3.

Projekt zbliżył się do celu, ponieważ ma już strukturę, kontrolę i ścieżkę publikacji. Nie zbliżył się jeszcze wystarczająco do zasadniczego źródła przewagi: **dużej liczby prawdziwych, aktualnych, użytecznych i łatwych do porównania danych**.

Obecna przewaga to kod. Docelowa przewaga musi być w danych, procesie ich aktualizacji i decyzjach, które użytkownik może dzięki nim podjąć.
