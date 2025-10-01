# SmartLunch – HACS (minimum)

**Cel:** tylko logowanie i utrzymanie sesji. Brak encji (jeszcze).

## Instalacja (HACS – Custom Repository)
1. Skopiuj repo do GitHuba i dodaj w HACS → Integrations → **Custom repositories** → URL repo jako *Integration*.
2. Zainstaluj, zrestartuj HA.
3. Ustawienia → Integracje → Dodaj integrację → **SmartLunch**.
4. Podaj email, hasło (i opcjonalnie własne `base`).

## Reauth / wygasanie sesji
- Każde wywołanie API próbuje **jedno ciche odświeżenie** (login) jeśli serwer zwróci 401/403/419.
- Jeśli ciche odświeżenie się nie uda – rzucamy `ConfigEntryAuthFailed` i HA poprosi o **ponowne uwierzytelnienie**.

## Co dalej?
- Dodanie `DataUpdateCoordinator` i pierwszych sensorów (np. saldo dofinansowania).
- UI OptionsFlow do ustawień (np. interwały, wybór domyślnego delivery_place).