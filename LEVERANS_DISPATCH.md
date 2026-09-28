# Snabb leverans – lägg in dispatch-token (Marcus, 5 minuter)

**Varför:** Leveransportalens jobb hämtas idag bara av GitHub Actions crontakten (`*/10 min`), men GitHub
stryper täta scheman i praktiken till några körningar per dygn (se NATTLOGG 2026-09-28/29, "DAGLIG TESTKUND").
En riktig köpare kunde alltså få vänta timmar på sin PDF.

**Vad som redan är klart:** Workern (`worker/src/index.js`) ber nu GitHub Actions köra direkt (`workflow_dispatch`)
så fort en order skapas i portalen – crontakten finns kvar oförändrad som reserv om det skulle strula. Det enda
som saknas är en token med rättighet att trigga just det anropet. Utan token gör koden ingenting extra (ofarligt
no-op) – portalen fungerar exakt som idag, bara utan snabbleveransen.

Tokenet får **bara** rättighet att starta workflows i det här enda repot – inget annat.

## Steg 1 – skapa token på GitHub

1. Öppna https://github.com/settings/personal-access-tokens/new (du måste vara inloggad som `guslinmarcus`).
2. **Token name:** `stjarnkarta-leverans-dispatch`
3. **Expiration:** `Custom...` → sätt datumet ett år fram (t.ex. 2027-09-28).
4. **Resource owner:** `guslinmarcus` (ditt eget konto, inte en organisation).
5. **Repository access:** välj `Only select repositories` → i listan som dyker upp, välj **enbart**
   `guslinmarcus/stjarnkarta-leverans`.
6. Fäll ut **Repository permissions** och sätt:
   - **Actions** → `Access: Read and write`
   - **Contents** → `Access: Read-only` (krävs inte alltid av GitHubs dispatch-endpoint, men kostar inget extra
     och gör tokenet garanterat kompatibelt om GitHub ändrar kravet).
   - Lämna alla andra rättigheter på `No access` (standardvärdet).
7. Scrolla ner och klicka **Generate token**.
8. Kopiera token-strängen som visas (börjar med `github_pat_...`). Den visas bara en gång.

## Steg 2 – lägg in token som Worker-secret

Kör i en terminal (byt ut `<KLISTRA_IN_TOKEN>` mot strängen du kopierade):

```
cd "agentbutik/leverans/worker"
npx wrangler secret put GH_DISPATCH_TOKEN
```

Wrangler frågar efter värdet interaktivt – klistra in token där och tryck Enter. Klart.

## Steg 3 – verifiera (valfritt men rekommenderat)

Skapa en teststjärnkarta via https://stjarnkarta-leverans.moodly-sverige.workers.dev/ (vilket ordernummer som
helst, t.ex. `9999999999`). Kolla sedan inom någon minut:

```
gh run list -R guslinmarcus/stjarnkarta-leverans --limit 3
```

Du ska se en ny körning av workflowet `leverera` med händelsen `workflow_dispatch`, startad inom loppet av
sekunder efter att du skickade formuläret (inte nästa jämna 10-minuterstakt).

## Om något går fel

- Ser du ingen ny `workflow_dispatch`-körning: kontrollera att secreten heter exakt `GH_DISPATCH_TOKEN`
  (`npx wrangler secret list` i `worker`-mappen visar namnen, inte värdena).
- Tokenet går ut om ett år (steg 1.3) – då slutar dispatchen fungera tyst (cron fortsätter som reserv) tills ett
  nytt token läggs in på samma sätt.
- Vill du stänga av snabbleveransen igen: `npx wrangler secret delete GH_DISPATCH_TOKEN` i `worker`-mappen.
  Cronen fortsätter leverera som idag.
