# Hlídač nabídky diecastcompany.nl

Skript pro vybrané výrobce stáhne z webu kompletní seznam položek, porovná ho
s minulým během a nahlásí:

- **PŘIDÁNO** – nové kódy, které tam minule nebyly
- **ZMIZELO** – kódy, které z výpisu výrobce zmizely (vyprodáno / stáhnuto)
- **ZMĚNA STAVU** – pre-order → skladem, posun měsíce dostupnosti, special offer…

Report se vypíše do konzole, uloží do `reports/` a pošle na Telegram a/nebo e-mailem.

## 1. Instalace (Windows / Mac / Linux)

```
pip install -r requirements.txt
```

## 2. Výběr výrobců

Vypiš, co web nabízí (názvy musí být přesně tak, jak je web používá):

```
python dcc_watch.py --list
```

a názvy zapiš do `config.json` → `manufacturers`. Předvyplněné jsou značky,
které vozíte (Mini GT, Tarmac, Pop Race, Inno, Para64, BBR, Tomica, Motorhelix, Hotwheels).

## 3. První spuštění

```
python dcc_watch.py --dry-run
```

První běh jen uloží aktuální stav do `state.json` (u každé značky napíše
"první načtení, uloženo N položek"). Od dalšího běhu už hlásí rozdíly.
Zkontroluj, že počet načtených položek zhruba odpovídá tomu, co web ukazuje
jako "hits" – pokud skript varuje, že načetl výrazně míň, dej vědět, upravím parser.

## 4. Upozornění

### Telegram (doporučuju – přijde to na mobil)
1. V Telegramu napiš botovi **@BotFather** → `/newbot` → dostaneš token.
2. Napiš svému novému botovi libovolnou zprávu (nebo ho přidej do skupiny s Honzou a Michalem).
3. Otevři v prohlížeči `https://api.telegram.org/bot<TOKEN>/getUpdates` a opiš `chat.id`
   (u skupiny je záporné číslo).
4. V `config.json` nastav `"telegram": {"enabled": true, "token": "...", "chat_ids": [123456]}`.

### E-mail
V `config.json` zapni `"email": {"enabled": true, ...}`. Pro Seznam/email.cz je
předvyplněný `smtp.seznam.cz:465`; heslo dej buď do `password`, nebo bezpečněji do
proměnné prostředí `SMTP_PASSWORD`.

## 5. Automatické spouštění

### Varianta A – GitHub Actions (zdarma, nic neběží u vás)
1. Vytvoř soukromý repozitář na GitHubu a nahraj do něj tuhle složku.
2. Settings → Secrets and variables → Actions → přidej `TELEGRAM_TOKEN`
   (a případně `SMTP_PASSWORD`). Token pak v `config.json` nech prázdný.
3. Workflow `.github/workflows/watch.yml` se spustí 3× denně a stav si ukládá zpět do repa.
   Čas upravíš v řádku `cron`. Ručně spustíš v záložce Actions → Run workflow.

### Varianta B – vlastní počítač
- **Windows:** Plánovač úloh → Vytvořit základní úlohu → denně → akce
  `python C:\cesta\dcc-watch\dcc_watch.py`. Počítač musí v tu dobu běžet.
- **Linux/Mac (cron):** `0 7,13,19 * * * cd /cesta/dcc-watch && python3 dcc_watch.py`

## Poznámky
- Skript stahuje jen veřejné stránky (bez přihlášení), ceny tedy nevidí.
  Šlo by dodělat přihlášení vaším B2B účtem – pak by se daly hlídat i ceny a sklad.
- Mezi požadavky čeká 1,5 s (`delay_seconds`), aby to jejich web nezatěžovalo.
  Deset značek = zhruba 100 stránek, tj. pár minut na jeden běh.
- Web u stránkování používá parametr `nobotcode`; skript si ho bere z odkazů
  na první stránce. Pokud by web začal blokovat, ozvi se.
- Historie všech reportů je ve složce `reports/`, kompletní stav (i to, co
  zmizelo, s datem) v `state.json`.

## 6. Webová aplikace (pro telefon i počítač, společná pro všechny)

Ve složce `docs/` je hotová aplikace: chipsy se značkami (každý si zaškrtne svoje),
záložka **Novinky** (co přibylo / zmizelo / změnilo stav, seřazené podle běhů, oranžová
tečka = neviděl jsi od minule) a **Katalog** (hledání v aktuální nabídce vybraných značek).
Hlídač do ní po každém běhu zapíše `docs/data.json` a `docs/history.json`.

Zprovoznění (jednou):
1. Na GitHubu v repu: Settings → Pages → Source: *Deploy from a branch*, Branch: `main`, složka `/docs` → Save.
2. Za minutu běží aplikace na `https://<tvoje-jméno>.github.io/<název-repa>/`.
3. Na mobilu otevři odkaz → Safari: Sdílet → *Přidat na plochu*; Chrome: menu ⋮ → *Přidat na plochu*.
   Pošli odkaz tátovi, udělá totéž. Od té chvíle to máte oba jako appku s ikonou.

Pozor: GitHub Pages fungují zdarma jen u **veřejného** repa (u soukromého jen s placeným
GitHub Pro). V repu není nic tajného – tokeny jsou v Secrets, ne v souborech – takže
veřejné repo je v pohodě. Kdybyste to chtěli mít neveřejné, jde to samé nasadit
zdarma na Cloudflare Pages nebo Netlify.

V `docs/` jsou teď ukázková data, ať se dá aplikace hned otevřít – první běh hlídače je přepíše.
